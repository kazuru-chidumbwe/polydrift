/* SIP consumer for replay (manuscript 3.6, lab L9), PJSIP pjsua-lib: places one call to URI and reports, one JSON
 * line per event, what the library tells its program: call state changes with the last status code, and the
 * media/SDP state when the call is confirmed. After WAIT_MS it hangs up whatever is left and prints a final summary.
 * The reason phrase is reported apart ("reason") so the comparison can leave it out (it is data, not an action).
 * Usage: pjconsumer URI WAIT_MS */
#include <pjsua-lib/pjsua.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void js(const pj_str_t *s) {
    putchar('"');
    for (int i = 0; i < (int)s->slen; i++) {
        unsigned char c = (unsigned char)s->ptr[i];
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c > 0x7e) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

static void on_call_state(pjsua_call_id id, pjsip_event *e) {
    pjsua_call_info ci;
    pjsua_call_get_info(id, &ci);
    printf("{\"ev\":\"state\",\"state\":");
    js(&ci.state_text);
    printf(",\"code\":%d,\"reason\":", ci.last_status);
    js(&ci.last_status_text);
    printf("}\n");
    fflush(stdout);
}

static void on_call_media_state(pjsua_call_id id) {
    pjsua_call_info ci;
    pjsua_call_get_info(id, &ci);
    printf("{\"ev\":\"media\",\"status\":%d}\n", (int)ci.media_status);
    fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int wait_ms = atoi(argv[2]);
    pjsua_create();
    pjsua_config cfg;
    pjsua_logging_config log;
    pjsua_media_config med;
    pjsua_config_default(&cfg);
    pjsua_logging_config_default(&log);
    pjsua_media_config_default(&med);
    log.console_level = 0;
    log.level = 0;
    cfg.cb.on_call_state = &on_call_state;
    cfg.cb.on_call_media_state = &on_call_media_state;
    if (pjsua_init(&cfg, &log, &med) != PJ_SUCCESS) { printf("{\"ev\":\"init_failed\"}\n"); return 0; }
    pjsua_transport_config tc;
    pjsua_transport_config_default(&tc);
    tc.port = 5070;
    pjsua_transport_id tid;
    if (pjsua_transport_create(PJSIP_TRANSPORT_UDP, &tc, &tid) != PJ_SUCCESS) { printf("{\"ev\":\"transport_failed\"}\n"); return 0; }
    pjsua_start();
    pjsua_set_null_snd_dev();
    pjsua_acc_id acc;
    pjsua_acc_add_local(tid, PJ_TRUE, &acc);
    pj_str_t uri = pj_str(argv[1]);
    pjsua_call_id cid;
    pj_status_t st = pjsua_call_make_call(acc, &uri, NULL, NULL, NULL, &cid);
    if (st != PJ_SUCCESS) printf("{\"ev\":\"make_call_failed\",\"status\":%d}\n", st);
    fflush(stdout);
    pj_thread_sleep(wait_ms);
    pjsua_call_info ci;
    int active = st == PJ_SUCCESS && pjsua_call_is_active(cid);
    if (active) {
        pjsua_call_get_info(cid, &ci);
        printf("{\"ev\":\"end\",\"active\":1,\"state\":");
        js(&ci.state_text);
        printf(",\"code\":%d}\n", ci.last_status);
    } else {
        printf("{\"ev\":\"end\",\"active\":0}\n");
    }
    fflush(stdout);
    pjsua_call_hangup_all();
    pj_thread_sleep(500);
    pjsua_destroy();
    return 0;
}
