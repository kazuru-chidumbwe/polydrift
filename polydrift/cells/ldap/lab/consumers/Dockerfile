# LDAP replay consumers (manuscript 3.6): OpenLDAP libldap through python-ldap, and ldap3. One image, two libraries.
# Build: docker build -t pd-consumer-ldap .
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libldap2-dev libsasl2-dev \
 && pip install --no-cache-dir python-ldap==3.4.4 ldap3==2.9.1 \
 && apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY ldapconsumer.py /ldapconsumer.py
RUN python3 -c "import ldap, ldap3; print('libldap', ldap.VENDOR_VERSION, 'python-ldap', ldap.__version__, 'ldap3', ldap3.__version__)" > /CONSUMER-VERSION
CMD ["sleep", "infinity"]
