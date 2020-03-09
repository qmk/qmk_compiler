# QMK Compiler Runbook

This document explains how to manage the QMK Compile service. It is assumed that you have access to the Rancher instance powering the QMK API. If you are using this guide to manage your personal installation you may need to make adjustments for your environment.

## Infrastructure Overview

The QMK infrastructure is built on top of [Rancher 1.6](https://rancher.com/docs/rancher/v1.6/en/). We use github authentication for access to the environment. When you were given access you were also given a "farmhouse" URL- this is the main entrypoint into our operational infrastructure.

The "Stacks" tab at the top is where you'll spend most of your time. If this is empty you may need to select the "Default" environment from the dropdown to the left of Stacks.

The QMK API services are in the `qmk-api` group. Please do not do anything with the other groups, they are Clueboard or infrastructure related and should not be touched without consulting @skullydazed.

## Architecture Diagram

![QMK API Architecture. API Clients speak HTTP to the load balancer, which distributes those requests to API nodes. Those nodes communicate with S3 and RQ to deliver information about jobs to clients. Worker nodes in the background pull jobs from RQ and store the results of those jobs in S3.](https://github.com/qmk/qmk_api/raw/master/docs/architecture.svg?sanitize=true)

## Services

This section will provide a brief overview of the services.

* `api`
    * The HTTP frontend to `api.qmk.fm`. All HTTP/HTTPS requests from the internet are handled here.
    * https://github.com/qmk/qmk_api
* `bot`
    * HTTP frontend for the bot, only used to receive webhooks from github.
    * https://github.com/qmk/qmk_bot
* `compiler`
    * Compiles a keymap for users. Pulls compile jobs from RQ, does the work, and then uploads the result to S3.
    * https://github.com/qmk/qmk_compiler
* `redis`
    * Key/value store. Used to store API data and as a queue for RQ.
    * https://redis.io/
    * https://github.com/qmk/qmk_api_redis
* `tasks`
    * Service that performs ongoing tasks to keep the API healthy.
    * https://github.com/qmk/qmk_api_tasks

# Runbook

For all of the services below you can drill down into them by clicking the service name from the dashboard. This will show you all the containers running that particular service. Each container has a hamburger menu, and from this menu you can do two of the most useful things- View Logs and Execute Shell.

Keep in mind that these are minimal docker containers. You may need to `apt update; apt install procps` to do some basic troubleshooting.

## api

This is a standard flask service, powered by gunicorn. You can tune the gunicorn startup parameters by "Upgrading" the service and setting these environment variables:

* `MAX_REQUESTS=1000`
    * Passed to `--max-requests`
* `MAX_REQUESTS_JITTER=100`
    * Passed to `--max-requests-jitter`
* `NUM_WORKERS=8`
    * Passed to `-w`
* `TIMEOUT=60`
    * Passed to `-t`

## bot

This is a standard flask service, powered by gunicorn. There are no tunables. Mostly this services chugs along unnoticed.

## compiler

Most of your troubleshooting time will be spent at the shell for this service. Drill down to the service, click the hamburger menu for any one of the running containers, and "Execute Shell". From here you have access to several scripts to help you manage the compile job queue.

Currently there's no easy way to see what job each compile node is running. For that you have to go down the list of containers and look at the log to see what the last entry is.

### ./test_keyboard <keyboard_name>

This script lets you test the compiling infrastructure even without the HTTP frontend being up. It will insert a compile job and monitor its progress so you can make sure everything is working.

### ./ls_jobs

This script lists the jobs that are waiting to run. This does not show any jobs that are currently being compiled.

### ./cancel_job <job_id>

This script will cancel a single job, by job_id. The job_id is the GUID printed out by `ls_jobs`.

### ./trigger_update

This will set `qmk_needs_update=True` in redis, which will cause `qmk_api_tasks` to refresh the API data.

### python3 ./update_kb_redis.py

This will refresh the API data immediately. This is needed in some bootstrap situations after redis has been restarted.

### ./flush_queue

This will flush the queue and delete all pending compiles. This will break anyone who is currently using QMK Configurator, please do not do this lightly.

## redis

Redis is our key/value store and queue for RQ. We have a custom configuration here: <https://github.com/qmk/qmk_api_redis>

If you need to restart redis for any reason the API data will need to be repopulated. Follow this procedure to restart redis:

1. Pause the `api` service by clicking the service hamburger menu and selecting "Stop". The API will begin to return 500 errors.
2. In a new tab open up a shell on one of the `compiler` containers. Keep this shell open.
3. Run `./flush_queue` in the shell
4. Watch the logs of the `compiler` instances, wait until they're all idle
5. Run `python3 ./update_kb_redis.py` in the shell
6. Wait for the API data to be generated
7. Use `./test_keyboard clueboard/66/rev3` to make sure the API data is good
8. Unpause the `api` service by clicking the service hamburger menu and selecting "Start". The API will resume working shortly.
9. Monitor the queue with `ls_jobs` to make sure things stay healthy.

## tasks

The `tasks` service does 3 main things- it inserts the `update_qmk_firmware` job, it inserts the `s3_cleanup` job, and it continually tests both infrastructure and keyboards to see if both are working. The results of these test compiles can be viewed here: <https://yanfali.github.io/qmk_error_page/>
