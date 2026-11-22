# 389 Directory Server 1.4.0.x for RQ5 L1 (389-DS #3586, fix b543627, first tag 389-ds-base-1.4.0.27): the Fedora 29
# release builds from Koji, installed against the archived Fedora 29 repositories. The 389ds/dirsrv images start at
# 1.4.1-era tooling (dscontainer), so this image creates the same instance on first start: suffix dc=lab,dc=test,
# port 3389, Directory Manager password from DS_DM_PASSWORD, then runs ns-slapd in the foreground.
# Build: docker build -f 389ds-140.Dockerfile --build-arg VER=1.4.0.27 -t pd-389ds:1.4.0.27 .
FROM fedora:29
ARG VER
RUN sed -i -e 's/^metalink=/#metalink=/' \
      -e 's|^#baseurl=https\?://[^/]*/pub/fedora/linux|baseurl=https://archives.fedoraproject.org/pub/archive/fedora/linux|' \
      /etc/yum.repos.d/*.repo \
 && K=https://kojipkgs.fedoraproject.org/packages/389-ds-base/${VER}/1.fc29 \
 && dnf -y --disablerepo='*modular*' --disablerepo='*testing*' --setopt=install_weak_deps=False install $K/x86_64/389-ds-base-${VER}-1.fc29.x86_64.rpm \
      $K/x86_64/389-ds-base-libs-${VER}-1.fc29.x86_64.rpm $K/noarch/python3-lib389-${VER}-1.fc29.noarch.rpm hostname \
 && dnf clean all && rpm -q 389-ds-base > /389DS-VERSION
COPY 389ds-140-start.sh /usr/local/bin/pd-389ds-start
COPY 389ds-140-systemctl.sh /usr/local/sbin/systemctl
RUN chmod +x /usr/local/bin/pd-389ds-start /usr/local/sbin/systemctl
EXPOSE 3389
ENTRYPOINT ["/usr/local/bin/pd-389ds-start"]
