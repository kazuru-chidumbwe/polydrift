/* SIP consumer for replay (manuscript 3.6, lab L9), Sofia-SIP nua: places one call to URI and reports, one JSON line
 * per event, what the library tells its program: responses to the INVITE (status), call state changes, and the
 * termination. After WAIT_MS it hangs up whatever is left. The reason phrase is reported apart ("reason").
 * Usage: sofconsumer URI WAIT_MS */
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
