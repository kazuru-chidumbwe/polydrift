package pd;

import java.io.FileInputStream;
import java.util.List;
import java.util.TreeSet;
import javax.mail.Session;
import javax.mail.internet.MimeMessage;
import org.apache.jsieve.ConfigurationManager;
import org.apache.jsieve.SieveFactory;
import org.apache.jsieve.mail.Action;
import org.apache.jsieve.mail.ActionFileInto;
import org.apache.jsieve.mail.ActionKeep;
import org.apache.jsieve.mail.ActionRedirect;
import org.apache.jsieve.mail.ActionReject;
import org.apache.jsieve.parser.generated.Node;
import org.apache.jsieve.util.check.ScriptCheckMailAdapter;

/**
 * jSieve intent runner for the polydrift Sieve cell: pd-jsieve SCRIPT MESSAGE ENV_FROM ENV_TO.
 * Prints one JSON line: compile (ok | refused) and intent, the actions jSieve queues on its check adapter.
 * jSieve has no delivery path of its own, so there is no effect layer.
 */
public final class JSieveRun {
  static String esc(String s) {
    return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\r", " ");
  }

  public static void main(String[] a) throws Exception {
    String ver = System.getenv().getOrDefault("PD_JSIEVE_VERSION", "?");
    SieveFactory factory = new ConfigurationManager().build();
    Node script;
    try (FileInputStream in = new FileInputStream(a[0])) {
      script = factory.parse(in);
    } catch (Throwable t) {
      System.out.println("{\"engine_version\":\"jsieve " + ver + "\",\"compile\":\"refused\",\"compile_text\":\""
          + esc(t.getClass().getSimpleName() + ": " + t.getMessage()) + "\"}");
      return;
    }
    MimeMessage msg = new MimeMessage(Session.getDefaultInstance(System.getProperties()), new FileInputStream(a[1]));
    ScriptCheckMailAdapter mail = new ScriptCheckMailAdapter();
    mail.setMail(msg);
    String rt = null;
    try {
      factory.evaluate(mail, script);
    } catch (Throwable t) {
      rt = t.getClass().getSimpleName() + ": " + t.getMessage();
    }
    TreeSet<String> intent = new TreeSet<>();
    List<Action> acts = mail.getActions();
    if (acts != null) {
      for (Action x : acts) {
        if (x instanceof ActionFileInto) intent.add("fileinto:" + ((ActionFileInto) x).getDestination());
        else if (x instanceof ActionRedirect) intent.add("redirect:" + ((ActionRedirect) x).getAddress());
        else if (x instanceof ActionReject) intent.add("reject");
        else if (x instanceof ActionKeep) intent.add("keep");
        else if (x.getClass().getSimpleName().equals("ActionDiscard")) intent.add("discard");
        else intent.add("other:" + x.getClass().getSimpleName());
      }
    }
    if (rt == null && intent.isEmpty()) intent.add("keep");
    StringBuilder sb = new StringBuilder();
    for (String s : intent) sb.append(sb.length() == 0 ? "" : ",").append('"').append(esc(s)).append('"');
    System.out.println("{\"engine_version\":\"jsieve " + ver + "\",\"compile\":\"ok\",\"intent\":[" + sb
        + "],\"runtime_error\":" + (rt == null ? "null" : "\"" + esc(rt) + "\"") + "}");
  }
}
