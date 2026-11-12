MESSAGE sip:bob@example.com SIP/2.0
Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bKmp
Max-Forwards: 70
From: <sip:alice@example.com>;tag=1928301774
To: <sip:bob@example.com>
Call-ID: pdr-mp@192.0.2.1
CSeq: 1 MESSAGE
Content-Type: multipart/mixed;boundary=pdb
Content-Length: 177

--pdb
Content-Type: application/sdp

v=0
o=a 1 1 IN IP4 192.0.2.1
s=-
c=IN IP4 192.0.2.1
t=0 0
m=audio 4000 RTP/AVP 0
--pdb
Content-Type: text/plain

note
--pdb--
