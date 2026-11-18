"""Сборка vless:// ссылки из настроек inbound'а 3X-UI."""
from __future__ import annotations

from urllib.parse import quote, urlencode


def client_flow(inbound: dict) -> str:
    """Та же логика, что у панели 3X-UI (inboundCanEnableTlsFlow)."""
    if inbound.get("disableFlow"):
        return ""
    s = inbound["stream"]
    if s.get("network", "tcp") == "tcp" and s.get("security") in ("reality", "tls"):
        return "xtls-rprx-vision"
    if (inbound["settings"].get("encryption") or "none") != "none":  # VLESS encryption (ML-KEM)
        return "xtls-rprx-vision"
    return ""


def transport_label(inbound: dict) -> str:
    net = inbound["stream"].get("network", "tcp")
    return {"tcp": "TCP", "xhttp": "XHTTP", "grpc": "gRPC", "ws": "WS", "httpupgrade": "HTTPUpgrade"}.get(net, net)


def build_vless(inbound: dict, uuid: str, host: str, remark: str) -> str:
    settings, s = inbound["settings"], inbound["stream"]
    net, sec = s.get("network", "tcp"), s.get("security", "none")
    q: dict[str, str] = {
        "type": net,
        "encryption": settings.get("encryption") or "none",  # ML-KEM, если включён в inbound
        "security": sec,
    }
    flow = client_flow(inbound)
    if flow:
        q["flow"] = flow

    if sec == "reality":
        r = s.get("realitySettings", {})
        rs = r.get("settings", {})
        q["pbk"] = rs.get("publicKey", "")
        q["fp"] = rs.get("fingerprint") or "chrome"
        q["sni"] = rs.get("serverName") or (r.get("serverNames") or [""])[0]
        q["sid"] = (r.get("shortIds") or [""])[0]
        q["spx"] = rs.get("spiderX", "")
        q["pqv"] = rs.get("mldsa65Verify", "")
    elif sec == "tls":
        t = s.get("tlsSettings", {})
        q["sni"] = t.get("serverName", "")
        q["fp"] = t.get("settings", {}).get("fingerprint") or "chrome"
        if t.get("alpn"):
            q["alpn"] = ",".join(t["alpn"])

    if net == "xhttp":
        x = s.get("xhttpSettings", {})
        q["path"] = x.get("path", "/")
        q["host"] = x.get("host", "")
        q["mode"] = x.get("mode", "auto")
    elif net == "ws":
        w = s.get("wsSettings", {})
        q["path"] = w.get("path", "/")
        q["host"] = w.get("host") or w.get("headers", {}).get("Host", "")
    elif net == "httpupgrade":
        h = s.get("httpupgradeSettings", {})
        q["path"], q["host"] = h.get("path", "/"), h.get("host", "")
    elif net == "grpc":
        g = s.get("grpcSettings", {})
        q["serviceName"] = g.get("serviceName", "")
        q["mode"] = "multi" if g.get("multiMode") else "gun"

    q = {k: v for k, v in q.items() if v}
    # Если в inbound'е задан External Proxy (так делают хостеры с пробросом
    # портов), берём внешний адрес и порт оттуда — как это делает сама панель.
    port = inbound["port"]
    ext = [e for e in (s.get("externalProxy") or []) if e.get("dest")]
    if ext:
        host, port = ext[0]["dest"], ext[0].get("port") or port
        if ext[0].get("forceTls") == "none":
            q["security"] = "none"
    addr = f"[{host}]" if ":" in host else host
    return f"vless://{uuid}@{addr}:{port}?{urlencode(q, quote_via=quote, safe=',/')}#{quote(remark)}"
