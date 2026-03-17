"""
freee会計 API クライアント

OAuth 2.0認証と取引・請求書のCRUDを提供します。

使い方:
    client = FreeeClient(
        client_id="YOUR_CLIENT_ID",
        client_secret="YOUR_CLIENT_SECRET",
        access_token="YOUR_ACCESS_TOKEN",  # 既存トークンがある場合
    )

    # 取引一覧取得
    deals = client.list_deals(company_id=1234)

    # 請求書作成
    invoice = client.create_invoice(company_id=1234, ...)
"""

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


BASE_URL = "https://api.freee.co.jp/api/1"
AUTH_URL = "https://accounts.secure.freee.co.jp/public_api/authorize"
TOKEN_URL = "https://accounts.secure.freee.co.jp/public_api/token"


@dataclass
class TokenInfo:
    access_token: str
    refresh_token: str
    expires_at: float  # UNIX timestamp


@dataclass
class FreeeClient:
    client_id: str
    client_secret: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    _expires_at: float = field(default=0.0, repr=False)

    # ------------------------------------------------------------------ #
    # OAuth helpers
    # ------------------------------------------------------------------ #

    def get_authorization_url(self, redirect_uri: str) -> str:
        """認可URLを返す。ブラウザでこのURLを開いてユーザーに許可してもらう。"""
        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        })
        return f"{AUTH_URL}?{params}"

    def fetch_token(self, code: str, redirect_uri: str) -> TokenInfo:
        """認可コードからアクセストークンを取得して保存する。"""
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        token = self._post_token(data)
        self._save_token(token)
        return token

    def refresh_access_token(self) -> TokenInfo:
        """リフレッシュトークンを使ってアクセストークンを更新する。"""
        if not self.refresh_token:
            raise ValueError("refresh_token が設定されていません")
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }
        token = self._post_token(data)
        self._save_token(token)
        return token

    def _post_token(self, data: dict) -> TokenInfo:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        resp = self._do_request(req)
        return TokenInfo(
            access_token=resp["access_token"],
            refresh_token=resp.get("refresh_token", self.refresh_token or ""),
            expires_at=time.time() + resp.get("expires_in", 21600),
        )

    def _save_token(self, token: TokenInfo) -> None:
        self.access_token = token.access_token
        self.refresh_token = token.refresh_token
        self._expires_at = token.expires_at

    def _ensure_token(self) -> None:
        """トークンが期限切れなら自動でリフレッシュする。"""
        if self.access_token and time.time() < self._expires_at - 60:
            return
        if self.refresh_token:
            self.refresh_access_token()
        elif not self.access_token:
            raise ValueError("access_token または refresh_token を設定してください")

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #

    def _do_request(self, req: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"HTTP {e.code}: {body}") from e

    def _request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
        self._ensure_token()
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.access_token}")
        req.add_header("Content-Type", "application/json")
        return self._do_request(req)

    # ------------------------------------------------------------------ #
    # 取引 (Deals) API
    # ------------------------------------------------------------------ #

    def list_deals(
        self,
        company_id: int,
        type: Optional[str] = None,         # "income" | "expense"
        start_issue_date: Optional[str] = None,  # YYYY-MM-DD
        end_issue_date: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """取引一覧を取得する。"""
        params = {
            "company_id": company_id,
            "type": type,
            "start_issue_date": start_issue_date,
            "end_issue_date": end_issue_date,
            "offset": offset,
            "limit": limit,
        }
        return self._request("GET", "/deals", params=params)["deals"]

    def create_deal(
        self,
        company_id: int,
        issue_date: str,           # YYYY-MM-DD
        type: str,                 # "income" | "expense"
        details: list[dict],
        ref_number: Optional[str] = None,
        payments: Optional[list[dict]] = None,
    ) -> dict:
        """取引を登録する。

        details の各要素:
            account_item_id (int), tax_code (int), amount (int), description (str)

        payments の各要素 (決済済みの場合):
            date (str), from_walletable_type (str), from_walletable_id (int), amount (int)
        """
        body: dict[str, Any] = {
            "company_id": company_id,
            "issue_date": issue_date,
            "type": type,
            "details": details,
        }
        if ref_number:
            body["ref_number"] = ref_number
        if payments:
            body["payments"] = payments
        return self._request("POST", "/deals", body={"deal": body})["deal"]

    def delete_deal(self, company_id: int, deal_id: int) -> None:
        """取引を削除する。"""
        self._request("DELETE", f"/deals/{deal_id}", params={"company_id": company_id})

    # ------------------------------------------------------------------ #
    # 請求書 (Invoices) API
    # ------------------------------------------------------------------ #

    def list_invoices(
        self,
        company_id: int,
        partner_id: Optional[int] = None,
        invoice_status: Optional[str] = None,  # "draft" | "submitted" | ...
        payment_status: Optional[str] = None,  # "unsettled" | "settled"
        start_issue_date: Optional[str] = None,
        end_issue_date: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[dict]:
        """請求書一覧を取得する。"""
        params = {
            "company_id": company_id,
            "partner_id": partner_id,
            "invoice_status": invoice_status,
            "payment_status": payment_status,
            "start_issue_date": start_issue_date,
            "end_issue_date": end_issue_date,
            "offset": offset,
            "limit": limit,
        }
        return self._request("GET", "/invoices", params=params)["invoices"]

    def create_invoice(
        self,
        company_id: int,
        issue_date: str,
        due_date: str,
        partner_id: int,
        invoice_contents: list[dict],
        title: Optional[str] = None,
        invoice_number: Optional[str] = None,
        invoice_status: str = "draft",
    ) -> dict:
        """請求書を作成する。

        invoice_contents の各要素:
            order (int), type (str), qty (float), unit (str),
            unit_price (int), description (str), tax_code (int)
        """
        body: dict[str, Any] = {
            "company_id": company_id,
            "issue_date": issue_date,
            "due_date": due_date,
            "partner_id": partner_id,
            "invoice_contents": invoice_contents,
            "invoice_status": invoice_status,
        }
        if title:
            body["title"] = title
        if invoice_number:
            body["invoice_number"] = invoice_number
        return self._request("POST", "/invoices", body={"invoice": body})["invoice"]

    def update_invoice(self, company_id: int, invoice_id: int, **kwargs) -> dict:
        """請求書を更新する（ステータス変更も可）。

        例: client.update_invoice(1234, 1, invoice_status="submitted")
        """
        body = {"company_id": company_id, **kwargs}
        return self._request("PUT", f"/invoices/{invoice_id}", body={"invoice": body})["invoice"]

    def delete_invoice(self, company_id: int, invoice_id: int) -> None:
        """請求書を削除する（draft のみ削除可）。"""
        self._request("DELETE", f"/invoices/{invoice_id}", params={"company_id": company_id})


# ------------------------------------------------------------------ #
# CLI / 動作確認用サンプル
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import os
    import sys

    # 環境変数からトークンを読み込む
    client = FreeeClient(
        client_id=os.environ.get("FREEE_CLIENT_ID", ""),
        client_secret=os.environ.get("FREEE_CLIENT_SECRET", ""),
        access_token=os.environ.get("FREEE_ACCESS_TOKEN"),
        refresh_token=os.environ.get("FREEE_REFRESH_TOKEN"),
    )
    company_id = int(os.environ.get("FREEE_COMPANY_ID", "0"))

    if not company_id:
        print("FREEE_COMPANY_ID を設定してください")
        sys.exit(1)

    print("=== 取引一覧 (直近20件) ===")
    deals = client.list_deals(company_id=company_id, limit=20)
    for d in deals:
        print(f"  [{d['id']}] {d['issue_date']} {d['type']:8s} ¥{d['amount']:>10,}  {d.get('partner_name', '')}")

    print("\n=== 未入金請求書 ===")
    invoices = client.list_invoices(company_id=company_id, payment_status="unsettled")
    for inv in invoices:
        print(f"  [{inv['id']}] {inv['issue_date']} {inv.get('invoice_number', '')}  ¥{inv['total_amount']:>10,}  {inv.get('partner_name', '')}")
