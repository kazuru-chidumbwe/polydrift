OPTIONS sip:bob@example.com SIP/2.0
Via: SIP/2.0/UDP 192.0.2.3:5060;branch=z9hG4bKr3, SIP/2.0/UDP 192.0.2.2:5060;branch=z9hG4bKr2
Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bKr1
Max-Forwards: 70
v: SIP/2.0/UDP 192.0.2.0:5060;branch=z9hG4bKr0
From: <sip:alice@example.com>;tag=19
To: <sip:bob@example.com>
Call-ID: pdr-via@192.0.2.1
CSeq: 1 OPTIONS
Content-Length: 0

