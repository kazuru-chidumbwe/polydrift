# ParDiff (Zheng et al., OOPSLA 2024) for the polydrift RQ2 side comparison.
# Toolchain as the authors document it: gcc 11, LLVM/clang 12.0.1, their Z3 fork, wllvm.
FROM ubuntu:22.04

ARG PARDIFF_SHA=b0a3098038914a510ccabfe95cdbf0a0b4edb609
ARG Z3_SHA=b0605a9d364d4d89e42713dd60baec604981aea2
ARG JOBS=4

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc-11 g++-11 cmake git ca-certificates python3 python3-pip \
        llvm-12 llvm-12-dev clang-12 libclang-12-dev zlib1g-dev libtinfo-dev libxml2-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir wllvm==1.3.1

RUN git clone https://github.com/zmw12306/z3 /src/z3 && cd /src/z3 && git checkout "$Z3_SHA" \
    && mkdir build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. \
    && make -j"$JOBS" && make install && ldconfig

# PARDIFF_BUILD=Release defines NDEBUG: at b0a3098 a build without it prints a trace of every visited instruction
# (DEBUG_FUNC in lib/Core/Executor.cpp is unconditional unless NDEBUG), 13 GB in 6 h on the MQTT pair (13 Nov).
# PARDIFF_PATCH names a file in patches/ applied before the build (Run B: skip-list entries, one reason each).
ARG PARDIFF_BUILD=Release
ARG PARDIFF_PATCH=""
COPY patches/ /patches/
RUN git clone https://github.com/zmw12306/ParDiff /src/pardiff && cd /src/pardiff && git checkout "$PARDIFF_SHA" \
    && if [ -n "$PARDIFF_PATCH" ]; then git apply "/patches/$PARDIFF_PATCH"; fi \
    && mkdir build && cd build && cmake -DCMAKE_BUILD_TYPE="$PARDIFF_BUILD" .. && make -j"$JOBS"

ENV PATH="/src/pardiff/build/bin:/usr/lib/llvm-12/bin:${PATH}" LLVM_COMPILER=clang
WORKDIR /work
