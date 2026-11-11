SIP/2.0 200 OK
Via: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bK-200-sdp
From: <sip:alice@example.com>;tag=a1
To: <sip:bob@example.com>;tag=b1
Call-ID: polydrift-200-sdp@example.com
CSeq: 1 INVITE
Contact: <sip:bob@192.0.2.2:5060>
Content-Type: application/sdp
Content-Length: 140

v=0
o=alice 2890844526 2890844526 IN IP4 192.0.2.1
s=polydrift
c=IN IP4 192.0.2.1
t=0 0
m=audio 49170 RTP/AVP 0
a=rtpmap:0 PCMU/8000
