// JMAP consumer for replay (manuscript 3.6, lab L9), go-jmap (git.sr.ht/~rockorager/go-jmap). Reads
// {"endpoint", "user", "password", "steps": [[[method, args, callId], ...], ...]} on stdin; each step is one API
// request whose calls are sent as given. Prints one JSON line per step: for each method response, the Go type
// go-jmap decoded it into and that value marshalled back (what a Go program sees), or the error a step returned.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"git.sr.ht/~rockorager/go-jmap"
	_ "git.sr.ht/~rockorager/go-jmap/core"
	_ "git.sr.ht/~rockorager/go-jmap/mail/email"
	_ "git.sr.ht/~rockorager/go-jmap/mail/emailsubmission"
	_ "git.sr.ht/~rockorager/go-jmap/mail/identity"
	_ "git.sr.ht/~rockorager/go-jmap/mail/mailbox"
	_ "git.sr.ht/~rockorager/go-jmap/mail/searchsnippet"
	_ "git.sr.ht/~rockorager/go-jmap/mail/thread"
)

type raw struct {
	name string
	args json.RawMessage
}

func (r *raw) Name() string                   { return r.name }
func (r *raw) Requires() []jmap.URI           { return nil }
func (r *raw) MarshalJSON() ([]byte, error)   { return r.args, nil }

type input struct {
	Endpoint string              `json:"endpoint"`
	User     string              `json:"user"`
	Password string              `json:"password"`
	Steps    [][][]json.RawMessage `json:"steps"`
}

func emit(v any) {
	b, _ := json.Marshal(v)
	fmt.Println(string(b))
}

func main() {
	var in input
	if err := json.NewDecoder(os.Stdin).Decode(&in); err != nil {
		emit(map[string]string{"input_error": err.Error()})
		return
	}
	c := &jmap.Client{SessionEndpoint: in.Endpoint}
	c.WithBasicAuth(in.User, in.Password)
	if err := c.Authenticate(); err != nil {
		emit(map[string]string{"session_error": fmt.Sprintf("%T", err), "msg": err.Error()})
		return
	}
	acct := ""
	if c.Session != nil {
		for _, id := range c.Session.PrimaryAccounts {
			acct = string(id)
			break
		}
	}
	for _, calls := range in.Steps {
		req := &jmap.Request{}
		for _, call := range calls {
			var name string
			_ = json.Unmarshal(call[0], &name)
			args := json.RawMessage(strings.ReplaceAll(string(call[1]), "{acct}", acct))
			req.Invoke(&raw{name: name, args: args})
		}
		resp, err := c.Do(req)
		if err != nil {
			emit(map[string]string{"error": fmt.Sprintf("%T", err), "msg": err.Error()})
			continue
		}
		out := []map[string]any{}
		for _, inv := range resp.Responses {
			b, merr := json.Marshal(inv.Args)
			item := map[string]any{"name": inv.Name, "id": inv.CallID, "type": fmt.Sprintf("%T", inv.Args)}
			if merr != nil {
				item["marshal_error"] = merr.Error()
			} else {
				item["args"] = json.RawMessage(b)
			}
			out = append(out, item)
		}
		emit(map[string]any{"responses": out})
	}
}
