
/* ---- polydrift L10: ParDiff entry for Mosquitto 2.0.22 (appended to src/handle_connect.c at build time) ----
 * The buffer is a whole MQTT packet, fixed header included, as NanoMQ's conn_handler takes it. The fixed header is
 * consumed with Mosquitto's own reader (packet__read_varint), as packet__read does on a socket, then the CONNECT
 * body goes to handle__connect on a fresh connection. Returns 0 when the broker accepts the CONNECT, 1 otherwise. */
void *pardiff_make_object(uint64_t size);
void *pardiff_make_message(void);
int pardiff_make_message_length(void);

int pardiff_main_message(void)
{
	struct mosquitto *ctx = pardiff_make_object(sizeof(struct mosquitto));
	unsigned char *pkt = pardiff_make_message();
	int len = pardiff_make_message_length();
	uint32_t rl = 0;
	uint8_t nbytes = 0;

	if(len < 2) return 1;
	if((pkt[0] & 0xF0) != CMD_CONNECT) return 1;
	ctx->listener = pardiff_make_object(sizeof(struct mosquitto__listener));
	ctx->state = mosq_cs_new;
	ctx->in_packet.command = pkt[0];
	ctx->in_packet.payload = pkt + 1;
	ctx->in_packet.remaining_length = (uint32_t)(len - 1);
	ctx->in_packet.pos = 0;
	if(packet__read_varint(&ctx->in_packet, &rl, &nbytes) != MOSQ_ERR_SUCCESS) return 1;
	if(rl != (uint32_t)(len - 1 - nbytes)) return 1;
	ctx->in_packet.payload = pkt + 1 + nbytes;
	ctx->in_packet.remaining_length = rl;
	ctx->in_packet.pos = 0;
	return handle__connect(ctx) == MOSQ_ERR_SUCCESS ? 0 : 1;
}
