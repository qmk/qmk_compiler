FROM debian
MAINTAINER Zach White <skullydazed@gmail.com>

RUN apt-get update && apt-get install --no-install-recommends -y \
    avr-libc \
    binutils-arm-none-eabi \
    binutils-avr \
    build-essential \
    clang \
    dfu-programmer \
    dfu-util \
    gcc \
    gcc-arm-none-eabi \
    gcc-avr \
    git \
    libnewlib-arm-none-eabi \
    python3 \
    python3-pip \
    unzip \
    wget \
    zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /
RUN git clone https://github.com/qmk/qmk_compiler_worker.git
WORKDIR /qmk_compiler_worker
RUN pip3 install -r requirements.txt
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
CMD rq worker -u redis://@redis.qmk-api:6379/0
