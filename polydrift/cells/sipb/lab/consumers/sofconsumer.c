/* SIP consumer for replay (manuscript 3.6, lab L9), Sofia-SIP nua: places one call to URI and reports, one JSON line
 * per event, what the library tells its program: responses to the INVITE (status), call state changes, and the
 * termination. After WAIT_MS it hangs up whatever is left. The reason phrase is reported apart ("reason").
 * Non-INVITE modes (METHOD = REGISTER, OPTIONS, MESSAGE): one request of that method and the final status nua
 * reports (REGISTER: also the Contact count in the answer; OPTIONS: the Allow methods).
 * Usage: sofconsumer URI WAIT_MS [METHOD] */
#include <sofia-sip/nua.h>
#include <sofia-sip/soa_tag.h>
#include <sofia-sip/sip_status.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static su_root_t *root;
static int shut = 0;

static void jstr(const char *s) {
    putchar('"');
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c > 0x7e) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

static void cb(nua_event_t event, int status, char const *phrase, nua_t *nua, nua_magic_t *magic,
               nua_handle_t *nh, nua_hmagic_t *hmagic, sip_t const *sip, tagi_t tags[]) {
    switch (event) {
    case nua_r_invite:
        printf("{\"ev\":\"r_invite\",\"code\":%d,\"reason\":", status);
        jstr(phrase);
        printf("}\n");
        break;
    case nua_i_state: {
        int cs = -1;
        tl_gets(tags, NUTAG_CALLSTATE_REF(cs), TAG_END());
        printf("{\"ev\":\"state\",\"state\":\"%s\",\"code\":%d}\n", nua_callstate_name(cs), status);
        break;
    }
    case nua_r_register:
    case nua_r_message:
    case nua_r_options: {
        if (status < 200) break;
        int n = 0;
        for (sip_contact_t const *m = sip ? sip->sip_contact : NULL; m; m = m->m_next) n++;
        printf("{\"ev\":\"%s\",\"code\":%d", event == nua_r_register ? "register" :
               event == nua_r_message ? "message" : "options", status);
        if (event == nua_r_register) printf(",\"contacts\":%d", n);
        if (event == nua_r_options) {
            printf(",\"allow\":[");
            int k = 0;
            if (sip && sip->sip_allow)
                for (int i = 0; sip->sip_allow->k_items && sip->sip_allow->k_items[i]; i++) {
                    if (k++) putchar(',');
                    jstr(sip->sip_allow->k_items[i]);
                }
            printf("]");
        }
        printf(",\"reason\":");
        jstr(phrase);
        printf("}\n");
        break;
    }
    case nua_r_bye:
        printf("{\"ev\":\"r_bye\",\"code\":%d}\n", status);
        break;
    case nua_i_terminated:
        printf("{\"ev\":\"terminated\",\"code\":%d}\n", status);
        break;
    case nua_r_shutdown:
        if (status >= 200) shut = 1;
        break;
    default:
        if (status >= 300 && event != nua_r_set_params) printf("{\"ev\":\"%s\",\"code\":%d}\n", nua_event_name(event), status);
        break;
    }
    fflush(stdout);
}

static void run_for(int ms) {
    struct timespec t0, t;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (;;) {
        su_root_step(root, 50);
        clock_gettime(CLOCK_MONOTONIC, &t);
        if ((t.tv_sec - t0.tv_sec) * 1000 + (t.tv_nsec - t0.tv_nsec) / 1000000 >= ms || shut) break;
    }
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    int wait_ms = atoi(argv[2]);
    su_init();
    root = su_root_create(NULL);
    nua_t *nua = nua_create(root, cb, NULL, NUTAG_URL("sip:0.0.0.0:5070"), TAG_END());
    if (!nua) { printf("{\"ev\":\"init_failed\"}\n"); return 0; }
    const char *method = argc > 3 ? argv[3] : "INVITE";
    if (strcmp(method, "INVITE") != 0) {
        nua_handle_t *h = nua_handle(nua, NULL, SIPTAG_TO_STR(argv[1]), SIPTAG_FROM_STR(argv[1]), TAG_END());
        if (strcmp(method, "REGISTER") == 0) {
            char reg[256];
            const char *at = strchr(argv[1], '@');
            snprintf(reg, sizeof reg, "sip:%s", at ? at + 1 : argv[1] + 4);
            nua_register(h, NUTAG_REGISTRAR(reg), NUTAG_M_USERNAME("alice"), TAG_END());
        } else if (strcmp(method, "MESSAGE") == 0) {
            nua_message(h, NUTAG_URL(argv[1]), SIPTAG_CONTENT_TYPE_STR("text/plain"), SIPTAG_PAYLOAD_STR("pd"), TAG_END());
        } else if (strcmp(method, "OPTIONS") == 0) {
            nua_options(h, NUTAG_URL(argv[1]), TAG_END());
        } else {
            printf("{\"ev\":\"unknown_method\"}\n");
        }
        fflush(stdout);
        run_for(wait_ms);
        printf("{\"ev\":\"end\"}\n");
        fflush(stdout);
        nua_shutdown(nua);
        shut = 0;
        run_for(2000);
        nua_destroy(nua);
        su_root_destroy(root);
        su_deinit();
        return 0;
    }
    nua_handle_t *nh = nua_handle(nua, NULL, SIPTAG_TO_STR(argv[1]), TAG_END());
    const char *sdp = "v=0\r\no=pd 1 1 IN IP4 0.0.0.0\r\ns=-\r\nc=IN IP4 0.0.0.0\r\nt=0 0\r\nm=audio 40000 RTP/AVP 0\r\n";
    nua_invite(nh, NUTAG_URL(argv[1]), SOATAG_USER_SDP_STR(sdp), TAG_END());
    run_for(wait_ms);
    printf("{\"ev\":\"end\"}\n");
    fflush(stdout);
    nua_bye(nh, TAG_END());
    run_for(500);
    nua_shutdown(nua);
    shut = 0;
    run_for(2000);
    nua_destroy(nua);
    su_root_destroy(root);
    su_deinit();
    return 0;
}
