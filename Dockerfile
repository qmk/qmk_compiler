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
    python3-setuptools \
    redis-tools \
    unzip \
    wget \
    zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /qmk_compiler
COPY . /qmk_compiler
RUN pip3 install -r requirements.txt
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
CMD ./bin/start_worker
