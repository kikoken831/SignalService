# based on tobix/pywine image - https://hub.docker.com/r/tobix/pywine
FROM hpdeandrade/pywine-staging:3.14

WORKDIR /mt5docker

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends cabextract xvfb x11vnc python3-websockify python3-numpy procps && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
