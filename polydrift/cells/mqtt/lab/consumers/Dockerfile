# MQTT replay consumers (manuscript 3.6): libmosquitto (C) and Eclipse Paho (Python). One image, two libraries.
# Build: docker build -t pd-consumer-mqtt .
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libmosquitto-dev python3 python3-pip \
 && pip install --no-cache-dir --break-system-packages paho-mqtt==2.1.0 && rm -rf /var/lib/apt/lists/*
COPY mosqconsumer.c pahoconsumer.py /src/
RUN gcc -O2 -Wall -o /usr/local/bin/mosqconsumer /src/mosqconsumer.c -lmosquitto \
 && echo "libmosquitto $(dpkg-query -W -f='${Version}' libmosquitto1) paho-mqtt $(python3 -c 'import paho.mqtt as p; print(p.__version__)')" > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
