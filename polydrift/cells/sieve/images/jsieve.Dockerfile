# Apache jSieve (apache-jsieve-core from Maven Central, exact release) behind the polydrift intent runner.
# Build: docker build -f jsieve.Dockerfile --build-arg VER=0.8 -t pd-jsieve:0.8 .
FROM maven:3-eclipse-temurin-17 AS build
ARG VER
WORKDIR /b
COPY jsieve-runner /b
RUN mvn -q -Djsieve.version=${VER} -DskipTests package dependency:copy-dependencies -DoutputDirectory=target/dep
FROM eclipse-temurin:17-jre
ARG VER
ENV PD_JSIEVE_VERSION=${VER}
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
COPY --from=build /b/target/jsieve-run-1.jar /opt/pd/jsieve-run.jar
COPY --from=build /b/target/dep /opt/pd/dep
RUN printf '#!/bin/sh\nexec java -cp "/opt/pd/jsieve-run.jar:/opt/pd/dep/*" pd.JSieveRun "$@"\n' > /usr/local/bin/pd-jsieve && chmod +x /usr/local/bin/pd-jsieve
