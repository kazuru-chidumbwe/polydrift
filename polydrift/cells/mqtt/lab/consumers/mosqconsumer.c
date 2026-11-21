/* MQTT consumer for replay (manuscript 3.6, lab L9), libmosquitto. Reads a script on stdin, one operation per line:
 *   connect VERSION CLEAN KEEPALIVE CLIENT_ID [sei=N] [rmax=N] [mps=N] [tam=N]
 *   subscribe QOS NO_LOCAL RETAIN_AS_PUBLISHED RETAIN_HANDLING TOPIC
 *   unsubscribe TOPIC
 *   publish QOS RETAIN TOPIC PAYLOAD_HEX
 *   disconnect
 * and prints what the library reports, one JSON object per line: connack, suback, unsuback, puback, message,
 * disconnect, and errors returned by the API calls. Usage: mosqconsumer HOST PORT < script */
#include <mosquitto.h>
#include <mqtt_protocol.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static void jstr(const char *s, int n) {
    putchar('"');
    for (int i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c > 0x7e) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

static void props(const mosquitto_property *p) {
    printf(",\"props\":[");
    int first = 1;
    for (; p; p = mosquitto_property_next(p)) {
        if (!first) putchar(',');
        printf("%d", mosquitto_property_identifier(p));
        first = 0;
    }
    putchar(']');
}

static void on_connect(struct mosquitto *m, void *o, int rc, int flags, const mosquitto_property *p) {
    printf("{\"ev\":\"connack\",\"rc\":%d,\"sp\":%d", rc, flags & 1);
    props(p);
    printf("}\n");
}

static void on_subscribe(struct mosquitto *m, void *o, int mid, int n, const int *g, const mosquitto_property *p) {
    printf("{\"ev\":\"suback\",\"rcs\":[");
    for (int i = 0; i < n; i++) printf("%s%d", i ? "," : "", g[i]);
    putchar(']');
    props(p);
    printf("}\n");
}

static void on_unsubscribe(struct mosquitto *m, void *o, int mid, const mosquitto_property *p) {
    printf("{\"ev\":\"unsuback\"");
    props(p);
    printf("}\n");
}

static void on_publish(struct mosquitto *m, void *o, int mid, int rc, const mosquitto_property *p) {
    printf("{\"ev\":\"puback\",\"rc\":%d}\n", rc);
}

static void on_message(struct mosquitto *m, void *o, const struct mosquitto_message *msg, const mosquitto_property *p) {
    printf("{\"ev\":\"message\",\"topic\":");
    jstr(msg->topic, (int)strlen(msg->topic));
    printf(",\"payload\":");
    jstr((const char *)msg->payload, msg->payloadlen);
    printf(",\"qos\":%d,\"retain\":%d", msg->qos, msg->retain ? 1 : 0);
    props(p);
    printf("}\n");
}

static void on_disconnect(struct mosquitto *m, void *o, int rc, const mosquitto_property *p) {
    printf("{\"ev\":\"disconnect\",\"rc\":%d}\n", rc);
}

static void pump(struct mosquitto *m, int ms) {
    struct timespec t0, t;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (;;) {
        int rc = mosquitto_loop(m, 50, 1);
        if (rc == MOSQ_ERR_NO_CONN || rc == MOSQ_ERR_CONN_LOST) {
            static int said = 0;
            if (!said) printf("{\"ev\":\"loop\",\"rc\":%d}\n", rc);
            said = 1;
        }
        clock_gettime(CLOCK_MONOTONIC, &t);
        if ((t.tv_sec - t0.tv_sec) * 1000 + (t.tv_nsec - t0.tv_nsec) / 1000000 >= ms) break;
    }
}

static int hexval(char c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    setvbuf(stdout, NULL, _IOLBF, 0);
    mosquitto_lib_init();
    struct mosquitto *m = NULL;
    char line[8192];
    while (fgets(line, sizeof line, stdin)) {
        char op[32] = "";
        sscanf(line, "%31s", op);
        if (!strcmp(op, "connect")) {
            int ver, clean, ka;
            char cid[512];
            if (sscanf(line, "connect %d %d %d %511s", &ver, &clean, &ka, cid) != 4) continue;
            m = mosquitto_new(strcmp(cid, "-") ? cid : NULL, clean, NULL);
            if (!m) { printf("{\"ev\":\"api\",\"call\":\"new\",\"rc\":-1}\n"); continue; }
            mosquitto_int_option(m, MOSQ_OPT_PROTOCOL_VERSION, ver == 5 ? MQTT_PROTOCOL_V5 : MQTT_PROTOCOL_V311);
            mosquitto_connect_v5_callback_set(m, on_connect);
            mosquitto_subscribe_v5_callback_set(m, on_subscribe);
            mosquitto_unsubscribe_v5_callback_set(m, on_unsubscribe);
            mosquitto_publish_v5_callback_set(m, on_publish);
            mosquitto_message_v5_callback_set(m, on_message);
            mosquitto_disconnect_v5_callback_set(m, on_disconnect);
            mosquitto_property *cp = NULL;
            char *s;
            if (ver == 5) {
                if ((s = strstr(line, "sei="))) mosquitto_property_add_int32(&cp, MQTT_PROP_SESSION_EXPIRY_INTERVAL, (uint32_t)atol(s + 4));
                if ((s = strstr(line, "rmax="))) mosquitto_property_add_int16(&cp, MQTT_PROP_RECEIVE_MAXIMUM, (uint16_t)atoi(s + 5));
                if ((s = strstr(line, "mps="))) mosquitto_property_add_int32(&cp, MQTT_PROP_MAXIMUM_PACKET_SIZE, (uint32_t)atol(s + 4));
                if ((s = strstr(line, "tam="))) mosquitto_property_add_int16(&cp, MQTT_PROP_TOPIC_ALIAS_MAXIMUM, (uint16_t)atoi(s + 4));
                if ((s = strstr(line, "rpi="))) mosquitto_property_add_byte(&cp, MQTT_PROP_REQUEST_PROBLEM_INFORMATION, (uint8_t)atoi(s + 4));
                if ((s = strstr(line, "rri="))) mosquitto_property_add_byte(&cp, MQTT_PROP_REQUEST_RESPONSE_INFORMATION, (uint8_t)atoi(s + 4));
                if ((s = strstr(line, " am="))) {
                    char am[256];
                    if (sscanf(s + 4, "%255s", am) == 1) mosquitto_property_add_string(&cp, MQTT_PROP_AUTHENTICATION_METHOD, am);
                }
            }
            if ((s = strstr(line, " will="))) {
                /* will=QOS,RETAIN,TOPIC,PAYLOAD_HEX (the topic may hold commas: the payload is after the last one) */
                char wv[8192];
                int wq = 0, wr = 0;
                if (sscanf(s + 6, "%8191s", wv) == 1 && sscanf(wv, "%d,%d,", &wq, &wr) == 2) {
                    char *topic = strchr(strchr(wv, ',') + 1, ',') + 1, *hx = strrchr(wv, ',');
                    *hx++ = 0;
                    int n = strcmp(hx, "-") ? (int)strlen(hx) / 2 : 0;
                    char *pl = malloc(n + 1);
                    for (int i = 0; i < n; i++) pl[i] = (char)(hexval(hx[2 * i]) * 16 + hexval(hx[2 * i + 1]));
                    int rc = mosquitto_will_set_v5(m, topic, n, pl, wq, wr, NULL);
                    if (rc) printf("{\"ev\":\"api\",\"call\":\"will\",\"rc\":%d}\n", rc);
                    free(pl);
                }
            }
            {
                char u[512] = "", p[512] = "";
                char *su = strstr(line, " user="), *sp = strstr(line, " pass=");
                if (su) sscanf(su + 6, "%511s", u);
                if (sp) sscanf(sp + 6, "%511s", p);
                if (su || sp) mosquitto_username_pw_set(m, su ? (strcmp(u, "-") ? u : "") : NULL,
                                                         sp ? (strcmp(p, "-") ? p : "") : NULL);
            }
            int rc = mosquitto_connect_bind_v5(m, argv[1], atoi(argv[2]), ka, NULL, cp);
            if (rc) printf("{\"ev\":\"api\",\"call\":\"connect\",\"rc\":%d}\n", rc);
            mosquitto_property_free_all(&cp);
            pump(m, 800);
        } else if (m && !strcmp(op, "subscribe")) {
            int qos, nl, rap, rh;
            char topic[4096];
            if (sscanf(line, "subscribe %d %d %d %d %4095s", &qos, &nl, &rap, &rh, topic) != 5) continue;
            int opts = qos | (nl ? MQTT_SUB_OPT_NO_LOCAL : 0) | (rap ? MQTT_SUB_OPT_RETAIN_AS_PUBLISHED : 0) | (rh << 4);
            int rc = mosquitto_subscribe_v5(m, NULL, topic, qos, opts & ~3, NULL);
            if (rc) printf("{\"ev\":\"api\",\"call\":\"subscribe\",\"rc\":%d}\n", rc);
            pump(m, 800);
        } else if (m && !strcmp(op, "unsubscribe")) {
            char topic[4096];
            if (sscanf(line, "unsubscribe %4095s", topic) != 1) continue;
            int rc = mosquitto_unsubscribe_v5(m, NULL, topic, NULL);
            if (rc) printf("{\"ev\":\"api\",\"call\":\"unsubscribe\",\"rc\":%d}\n", rc);
            pump(m, 800);
        } else if (m && !strcmp(op, "publish")) {
            int qos, retain;
            char topic[4096], hex[4096];
            hex[0] = 0;
            if (sscanf(line, "publish %d %d %4095s %4095s", &qos, &retain, topic, hex) < 3) continue;
            if (!strcmp(hex, "-")) hex[0] = 0;
            int n = (int)strlen(hex) / 2;
            char *pl = malloc(n + 1);
            for (int i = 0; i < n; i++) pl[i] = (char)(hexval(hex[2 * i]) * 16 + hexval(hex[2 * i + 1]));
            int rc = mosquitto_publish_v5(m, NULL, topic, n, pl, qos, retain, NULL);
            free(pl);
            if (rc) printf("{\"ev\":\"api\",\"call\":\"publish\",\"rc\":%d}\n", rc);
            pump(m, 800);
        } else if (m && !strcmp(op, "disconnect")) {
            mosquitto_disconnect_v5(m, 0, NULL);
            pump(m, 300);
        }
    }
    if (m) {
        pump(m, 1500);
        mosquitto_destroy(m);
    }
    mosquitto_lib_cleanup();
    return 0;
}
