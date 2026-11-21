#!/usr/bin/env bash
# polydrift L10 on Testbed D (21 Nov 2026): ParDiff b0a3098 Release build, MQTT pair Mosquitto 2.0.22 vs NanoMQ
# 0.25.7 (the campaign MQTT cell's current versions). Run A: as published (no edits). Run B: Run A plus the skip-list
# patch patches/runB-mosquitto-skip.patch. Each run capped at 6 h. Run by the campaign job plan (tools/campaign.py jobs).
# Usage: run_mqtt_ab.sh HARNESS_DIR OUT_DIR. Build context: HARNESS_DIR/campaign/pardiff (Dockerfile, patches, entry
# wrappers). Sources are fetched into OUT_DIR/src. Results in OUT_DIR/mqtt; flag OUT_DIR/MQTT-AB-DONE.
set -uo pipefail
CTX=$1/campaign/pardiff; D=$2; O=$D/mqtt; mkdir -p $D/src $O; rm -f $D/MQTT-AB-DONE
log(){ echo "[$(date -u +%FT%TZ)] $*" >> $O/progress; }
log start
cd $CTX
for v in A B; do
  P=""; [ $v = B ] && P=runB-mosquitto-skip.patch
  docker image inspect polydrift-pardiff:b0a3098-rel-$v >/dev/null 2>&1 || \
    nice -n 5 docker build --network host --build-arg JOBS=8 --build-arg PARDIFF_PATCH=$P -t polydrift-pardiff:b0a3098-rel-$v . > $O/image-$v.log 2>&1 \
    || { log "image $v FAILED"; echo "IMAGE-$v-FAILED" > $D/MQTT-AB-DONE; exit 1; }
  log "image $v $(docker image inspect -f '{{.Id}}' polydrift-pardiff:b0a3098-rel-$v | cut -c1-19)"
done
cd $D/src
[ -d mosquitto-2.0.22 ] || { curl -fsSLO https://mosquitto.org/files/source/mosquitto-2.0.22.tar.gz && tar xzf mosquitto-2.0.22.tar.gz; }
[ -d nanomq ] || git clone -q --branch 0.25.7 --recursive https://github.com/nanomq/nanomq.git nanomq
sha256sum mosquitto-2.0.22.tar.gz > $O/sources.sha256; (cd nanomq && echo "nanomq $(git rev-parse HEAD) nng $(git -C nng rev-parse HEAD)") >> $O/sources.sha256
log "sources ready"
cp $CTX/entry-mosquitto.c $CTX/entry-nanomq.c $O/
docker run --rm --cpus=4 -v $D/src:/srcro:ro -v $O:/out polydrift-pardiff:b0a3098-rel-A bash -c '
export LLVM_COMPILER=clang LLVM_CC_NAME=clang-12 LLVM_CXX_NAME=clang++-12 LLVM_LINK_NAME=llvm-link-12 LLVM_AR_NAME=llvm-ar-12
apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq --no-install-recommends file libssl-dev libcjson-dev >/dev/null 2>&1
mkdir -p /b && cp -a /srcro/mosquitto-2.0.22 /srcro/nanomq /b/
cd /b/mosquitto-2.0.22 && cat /out/entry-mosquitto.c >> src/handle_connect.c \
 && mkdir build && cd build && CC=wllvm CFLAGS="-O0 -g" cmake .. -DCMAKE_EXE_LINKER_FLAGS="-Wl,--unresolved-symbols=ignore-all" -DWITH_CJSON=OFF -DDOCUMENTATION=OFF \
      -DWITH_STATIC_LIBRARIES=OFF -DWITH_CLIENTS=OFF -DWITH_APPS=OFF -DWITH_PLUGINS=OFF > /out/build-mosquitto.log 2>&1 \
 && make -j4 mosquitto >> /out/build-mosquitto.log 2>&1 && extract-bc -o /out/mosquitto.bc src/mosquitto >> /out/build-mosquitto.log 2>&1
cd /b/nanomq && cat /out/entry-nanomq.c >> nng/src/sp/protocol/mqtt/mqtt_parser.c \
 && mkdir build && cd build && CC=wllvm CXX=wllvm++ CFLAGS="-O0 -g" cmake .. -DCMAKE_EXE_LINKER_FLAGS="-Wl,--unresolved-symbols=ignore-all" -DNNG_ENABLE_TLS=OFF -DBUILD_CLIENT=OFF \
      -DENABLE_JWT=OFF -DNNG_TESTS=OFF -DBUILD_BENCH=OFF > /out/build-nanomq.log 2>&1 \
 && make -j4 nanomq >> /out/build-nanomq.log 2>&1 && extract-bc -o /out/nanomq.bc $(find . -type f -name nanomq -perm -u+x | head -1) >> /out/build-nanomq.log 2>&1
ls -la /out/*.bc' > $O/bitcode.log 2>&1
[ -s $O/mosquitto.bc ] && [ -s $O/nanomq.bc ] || { log "bitcode missing"; echo "BITCODE-MISSING" > $D/MQTT-AB-DONE; exit 1; }
sha256sum $O/*.bc >> $O/sources.sha256; log "bitcode ready"
for v in A B; do
  docker run --rm --cpus=2 -v $O:/out polydrift-pardiff:b0a3098-rel-$v bash -c '
cd /src/pardiff/build; TIMEFORMAT="wall=%R user=%U sys=%S"
ulimit -f 4194304   # output capped at 4 GiB per run (disk guard; exit 153 = SIGXFSZ when the cap is hit)
{ time timeout 6h ./bin/pardiff /out/mosquitto.bc /out/nanomq.bc -pardiff-entry=pardiff_main_message > /out/run'$v'-out.txt 2>&1; echo "pardiff_exit=$?"; } 2>&1' > $O/run$v.log 2>&1 &
done
wait
for v in A B; do
  f=$O/run$v-out.txt
  echo "run$v $(grep -E 'pardiff_exit|wall=' $O/run$v.log | tr '\n' ' ') bytes=$(stat -c %s $f) diff_lines=$(grep -c '^diff:' $f)" >> $O/SUMMARY.txt
  [ $(stat -c %s $f) -gt 2000000000 ] && { head -c 2000000 $f > $f.head2MB; gzip -f $f; }
done
sha256sum $O/run*-out.txt* >> $O/SUMMARY.txt 2>/dev/null
log DONE; cat $O/SUMMARY.txt > $D/MQTT-AB-DONE
