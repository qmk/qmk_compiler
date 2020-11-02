FROM qmkfm/base_container
MAINTAINER Zach White <skullydazed@gmail.com>

RUN apt-get update && apt-get install --no-install-recommends -y \
    clang \
    python3-dev \
    redis-tools && rm -rf /var/lib/apt/lists/*

WORKDIR /qmk_compiler
COPY . /qmk_compiler
RUN pip3 install -r requirements.txt
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
CMD ./bin/start_worker
