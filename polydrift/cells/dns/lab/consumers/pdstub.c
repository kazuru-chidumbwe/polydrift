/* DNS consumer for replay (manuscript 3.6, lab L9): one stub-resolver lookup through the C library under test
 * (glibc or musl, same source), reported as one JSON line. What a program using the library would see: the
 * res_query result (h_errno on failure), the header rcode and TC bit, the answer records as the library's parser
 * reads them (ns_initparse/ns_parserr, names through dn_expand), and for A and AAAA the getaddrinfo result and the
 * address order. Usage: pdstub NAME QTYPE_NUMBER */
#include <arpa/inet.h>
#include <arpa/nameser.h>
#include <netdb.h>
#include <netinet/in.h>
#include <resolv.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

static void jstr(const char *s) {
    putchar('"');
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c > 0x7e) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    const char *name = argv[1];
    int type = atoi(argv[2]);
    static unsigned char buf[65536];
    res_init();
    int n = res_query(name, ns_c_in, type, buf, sizeof buf);
    printf("{\"res_query\":%s,\"h_errno\":%d", n < 0 ? "\"fail\"" : "\"ok\"", n < 0 ? h_errno : 0);
    if (n >= 12) {
        printf(",\"rcode\":%d,\"tc\":%d", buf[3] & 0x0f, (buf[2] >> 1) & 1);
        ns_msg m;
        if (ns_initparse(buf, n, &m) == 0) {
            int an = ns_msg_count(m, ns_s_an);
            printf(",\"answers\":[");
            for (int i = 0; i < an; i++) {
                ns_rr rr;
                if (i) putchar(',');
                if (ns_parserr(&m, ns_s_an, i, &rr) != 0) { printf("\"unparsed\""); continue; }
                char line[2048], rd[1100];
                const unsigned char *p = ns_rr_rdata(rr);
                int t = ns_rr_type(rr), len = ns_rr_rdlen(rr);
                if (t == ns_t_a && len == 4) inet_ntop(AF_INET, p, rd, sizeof rd);
                else if (t == ns_t_aaaa && len == 16) inet_ntop(AF_INET6, p, rd, sizeof rd);
                else if (t == ns_t_cname || t == 39 || t == ns_t_ns || t == ns_t_ptr) {
                    if (dn_expand(ns_msg_base(m), ns_msg_end(m), p, rd, sizeof rd) < 0) strcpy(rd, "?");
                } else {
                    int k = 0;
                    for (int j = 0; j < len && k < (int)sizeof rd - 3; j++) k += snprintf(rd + k, sizeof rd - k, "%02x", p[j]);
                    rd[k] = 0;
                }
                snprintf(line, sizeof line, "%s %d %s", ns_rr_name(rr), t, rd);
                jstr(line);
            }
            putchar(']');
        } else {
            printf(",\"parse\":\"fail\"");
        }
    }
    if (type == 1 || type == 28) {
        struct addrinfo h, *res = NULL, *a;
        memset(&h, 0, sizeof h);
        h.ai_family = type == 1 ? AF_INET : AF_INET6;
        h.ai_socktype = SOCK_STREAM;
        int e = getaddrinfo(name, NULL, &h, &res);
        printf(",\"gai\":%d,\"addrs\":[", e);
        int first = 1;
        for (a = e == 0 ? res : NULL; a; a = a->ai_next) {
            char s[64];
            void *ad = a->ai_family == AF_INET ? (void *)&((struct sockaddr_in *)a->ai_addr)->sin_addr
                                               : (void *)&((struct sockaddr_in6 *)a->ai_addr)->sin6_addr;
            inet_ntop(a->ai_family, ad, s, sizeof s);
            if (!first) putchar(',');
            jstr(s);
            first = 0;
        }
        putchar(']');
        if (res) freeaddrinfo(res);
    }
    printf("}\n");
    return 0;
}
