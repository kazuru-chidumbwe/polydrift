OPTIONS sip:bob@example.com SIP/2.0
Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bKau
Max-Forwards: 70
From: <sip:alice@example.com>;tag=1928301774
To: <sip:bob@example.com>
Call-ID: pdr-au@192.0.2.1
CSeq: 1 OPTIONS
Authorization: Digest username="a", realm="r1", nonce="n", uri="sip:bob@example.com", response="0"
Authorization: Digest username="a", realm="r2", nonce="n", uri="sip:bob@example.com", response="0"
Proxy-Authorization: Digest username="a", realm="p1", nonce="n", uri="sip:bob@example.com", response="0"
Proxy-Authorization: Digest username="a", realm="p2", nonce="n", uri="sip:bob@example.com", response="0"
Content-Length: 0

