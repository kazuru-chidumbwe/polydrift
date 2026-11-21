
/* ---- polydrift L10: ParDiff entry for NanoMQ 0.25.7 / NanoNNG (appended to src/sp/protocol/mqtt/mqtt_parser.c) ----
 * The buffer is a whole MQTT packet, fixed header included, which is what conn_handler takes. Returns 0 when
 * conn_handler accepts the CONNECT, 1 otherwise. */
void *pardiff_make_object(uint64_t size);
void *pardiff_make_message(void);
int pardiff_make_message_length(void);

int pardiff_main_message(void)
{
	conn_param *cparam = pardiff_make_object(sizeof(conn_param));
	uint8_t *pkt = pardiff_make_message();
	int len = pardiff_make_message_length();

	if(len < 2) return 1;
	return conn_handler(pkt, cparam, (size_t) len) == 0 ? 0 : 1;
}
