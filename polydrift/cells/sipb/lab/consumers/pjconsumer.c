/* SIP consumer for replay (manuscript 3.6, lab L9), PJSIP pjsua-lib: places one call to URI and reports, one JSON
 * line per event, what the library tells its program: call state changes with the last status code, and the
 * media/SDP state when the call is confirmed. After WAIT_MS it hangs up whatever is left and prints a final summary.
 * The reason phrase is reported apart ("reason") so the comparison can leave it out (it is data, not an action).
 * Non-INVITE modes (METHOD = REGISTER, OPTIONS, MESSAGE): one request of that method and the final status the
 * library reports (REGISTER: also the binding count and expiry it reads back; OPTIONS: the Allow methods).
 * Usage: pjconsumer URI WAIT_MS [METHOD] */
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

static void on_reg_state2(pjsua_acc_id id, pjsua_reg_info *info) {
    struct pjsip_regc_cbparam *p = info->cbparam;
    printf("{\"ev\":\"register\",\"code\":%d,\"expires\":%d,\"contacts\":%u,\"reason\":", p->code,
           (int)p->expiration, p->contact_cnt);
    js(&p->reason);
    printf("}\n");
    fflush(stdout);
}

static void on_pager_status(pjsua_call_id call_id, const pj_str_t *to, const pj_str_t *body, void *user_data,
                            pjsip_status_code status, const pj_str_t *reason) {
    printf("{\"ev\":\"message\",\"code\":%d,\"reason\":", (int)status);
    js(reason);
    printf("}\n");
    fflush(stdout);
}

static void on_options(void *token, pjsip_event *e) {
    if (e->type != PJSIP_EVENT_TSX_STATE) return;
    pjsip_transaction *tsx = e->body.tsx_state.tsx;
    printf("{\"ev\":\"options\",\"code\":%d,\"allow\":[", tsx->status_code);
    if (e->body.tsx_state.type == PJSIP_EVENT_RX_MSG) {
        pjsip_msg *msg = e->body.tsx_state.src.rdata->msg_info.msg;
        pjsip_allow_hdr *a = (pjsip_allow_hdr *)pjsip_msg_find_hdr(msg, PJSIP_H_ALLOW, NULL);
        for (unsigned i = 0; a && i < a->count; i++) {
            if (i) putchar(',');
            js(&a->values[i]);
        }
    }
    printf("],\"reason\":");
    js(&tsx->status_text);
    printf("}\n");
    fflush(stdout);
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int wait_ms = atoi(argv[2]);
    pj_log_set_level(0);
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
    cfg.cb.on_reg_state2 = &on_reg_state2;
    cfg.cb.on_pager_status = &on_pager_status;
    const char *method = argc > 3 ? argv[3] : "INVITE";
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
    if (strcmp(method, "INVITE") != 0) {
        if (strcmp(method, "REGISTER") == 0) {
            /* registrar: the host part of the address of record */
            char reg[256];
            const char *at = strchr(argv[1], '@');
            snprintf(reg, sizeof reg, "sip:%s", at ? at + 1 : argv[1] + 4);
            pjsua_acc_config ac;
            pjsua_acc_config_default(&ac);
            ac.id = uri;
            ac.reg_uri = pj_str(reg);
            ac.register_on_acc_add = PJ_TRUE;
            ac.reg_retry_interval = 0;
            pjsua_acc_id racc;
            if (pjsua_acc_add(&ac, PJ_TRUE, &racc) != PJ_SUCCESS) printf("{\"ev\":\"acc_add_failed\"}\n");
        } else if (strcmp(method, "MESSAGE") == 0) {
            pj_str_t text = pj_str("pd");
            if (pjsua_im_send(acc, &uri, NULL, &text, NULL, NULL) != PJ_SUCCESS) printf("{\"ev\":\"send_failed\"}\n");
        } else if (strcmp(method, "OPTIONS") == 0) {
            pjsip_endpoint *ep = pjsua_get_pjsip_endpt();
            pjsip_tx_data *td;
            pj_str_t from = pj_str("<sip:alice@172.31.64.11:5070>");
            if (pjsip_endpt_create_request(ep, &pjsip_options_method, &uri, &from, &uri, NULL, NULL, -1, NULL, &td)
                    != PJ_SUCCESS || pjsip_endpt_send_request(ep, td, -1, NULL, &on_options) != PJ_SUCCESS)
                printf("{\"ev\":\"send_failed\"}\n");
        } else {
            printf("{\"ev\":\"unknown_method\"}\n");
        }
        fflush(stdout);
        pj_thread_sleep(wait_ms);
        printf("{\"ev\":\"end\"}\n");
        fflush(stdout);
        pjsua_destroy();
        return 0;
    }
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
