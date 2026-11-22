# linphonec from the upstream linphone-sdk release tag (the Debian builds stop at 5.3.105). Audio only, console UI,
# no video, no LIME, no LDAP, no FlexiAPI. Same entrypoint as the Debian image (linphone-run.sh).
# Build: docker build -f linphone-sdk.Dockerfile --build-arg SDK_TAG=5.5.41 -t pd-sipb-linphone:5.5.41 .
FROM debian:trixie-slim
ARG SDK_TAG=5.5.41
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates cmake ninja-build build-essential pkg-config perl python3 python3-pystache python3-six \
      yasm nasm doxygen libasound2-dev libpulse-dev libv4l-dev libxml2-dev libsqlite3-dev zlib1g-dev socat \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --branch ${SDK_TAG} --recurse-submodules -j8 https://gitlab.linphone.org/BC/public/linphone-sdk.git /src \
 && git -C /src rev-parse HEAD > /LINPHONE-SDK-COMMIT
# hidapi (bundled) needs libudev at configure time
RUN apt-get update && apt-get install -y --no-install-recommends libudev-dev && rm -rf /var/lib/apt/lists/*
RUN cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/linphone \
      -DENABLE_VIDEO=OFF -DENABLE_ADVANCED_IM=OFF -DENABLE_LIME_X3DH=OFF -DENABLE_LDAP=OFF -DENABLE_FLEXIAPI=OFF \
      -DENABLE_QRCODE=OFF -DENABLE_JPEG=OFF -DENABLE_UNIT_TESTS=OFF -DENABLE_DOC=OFF -DENABLE_CONSOLE_UI=ON \
      -DENABLE_TOOLS=ON -DENABLE_GPL_THIRD_PARTIES=ON -DENABLE_NON_FREE_FEATURES=OFF \
 && cmake --build /build --parallel \
 && cmake --install /build \
 && rm -rf /build
ENV PATH=/opt/linphone/bin:$PATH LD_LIBRARY_PATH=/opt/linphone/lib:/opt/linphone/lib64
RUN linphonec --version 2>&1 | head -1 > /LINPHONE-VERSION; echo "linphone-sdk ${SDK_TAG}" >> /LINPHONE-VERSION; cat /LINPHONE-VERSION
COPY linphone-run.sh /usr/local/bin/linphone-run.sh
RUN chmod +x /usr/local/bin/linphone-run.sh
ENTRYPOINT ["/usr/local/bin/linphone-run.sh"]
