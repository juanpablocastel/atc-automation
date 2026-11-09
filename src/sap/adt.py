"""Llamadas a los servicios ADT (los mismos que usa Eclipse) a través de RFC.

Eclipse no usa HTTP con sistemas on-premise: envía cada petición REST de ADT
dentro del módulo de función SADT_REST_RFC_ENDPOINT. Aquí hacemos lo mismo.
"""
from dataclasses import dataclass

FM_ADT = "SADT_REST_RFC_ENDPOINT"


@dataclass
class AdtResponse:
    status: int
    reason: str
    headers: dict
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def adt_request(conn, method: str, uri: str, headers: dict | None = None,
                body: bytes = b"") -> AdtResponse:
    """Envía una petición ADT (GET, POST, PUT...) y devuelve la respuesta."""
    request = {
        "REQUEST_LINE": {"METHOD": method, "URI": uri, "VERSION": "HTTP/1.1"},
        "HEADER_FIELDS": [{"NAME": k, "VALUE": v} for k, v in (headers or {}).items()],
        "MESSAGE_BODY": body,
    }
    result = conn.call(FM_ADT, REQUEST=request)
    resp = result["RESPONSE"]
    status_line = resp["STATUS_LINE"]
    return AdtResponse(
        status=int(status_line["STATUS_CODE"]),
        reason=status_line["REASON_PHRASE"],
        headers={h["NAME"]: h["VALUE"] for h in resp["HEADER_FIELDS"]},
        body=resp["MESSAGE_BODY"] or b"",
    )


def fetch_csrf(conn) -> str | None:
    """Pide un token CSRF (necesario en algunos sistemas para POST/PUT)."""
    r = adt_request(conn, "GET", "/sap/bc/adt/discovery",
                    {"x-csrf-token": "fetch", "Accept": "application/atomsvc+xml"})
    for k, v in r.headers.items():
        if k.lower() == "x-csrf-token":
            return v
    return None


def describir_interfaz(conn) -> None:
    """Imprime los parámetros del módulo ADT; sirve para diagnosticar errores."""
    desc = conn.get_function_description(FM_ADT)
    for p in desc.parameters:
        print(f"  {p['direction']:<10} {p['name']}")
