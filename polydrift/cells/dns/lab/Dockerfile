# DNS cell lab image: lab authoritative servers (auth.py) and the client driver (driver.py).
# Code is mounted read-only at /lab at run time; the image carries only the pinned libraries.
FROM python:3.12-slim
RUN pip install --no-cache-dir dnspython==2.7.0 pyyaml==6.0.2
WORKDIR /lab
