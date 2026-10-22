"""Optional generic MCP App call proofs. No integration-specific policy lives here."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import time
import uuid


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def arguments_digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ProofSigner:
    def __init__(self, private_key, key_id, host_id, principal_id):
        self.private_key, self.key_id, self.host_id, self.principal_id = (
            private_key,
            key_id,
            host_id,
            principal_id,
        )

    def sign_call(
        self,
        *,
        audience,
        session_id,
        thread_id,
        workspace_ref,
        app_instance_id,
        tool,
        arguments,
        call_id,
        interaction=None,
        now=None,
    ):
        now = time.time() if now is None else now
        digest = arguments_digest(arguments)
        claims = dict(
            version="codex-host-call/v1",
            host_id=self.host_id,
            principal_id=self.principal_id,
            audience=audience,
            session_id=session_id,
            thread_id=thread_id,
            workspace_ref=workspace_ref,
            app_instance_id=app_instance_id,
            tool=tool,
            arguments_digest=digest,
            call_id=call_id,
            nonce=call_id,
            issued_at=now,
            expires_at=now + 120,
        )
        if interaction:
            claims["interaction"] = {**interaction, "arguments_digest": digest, "decided_at": now}
        return {
            "algorithm": "Ed25519",
            "key_id": self.key_id,
            "claims": claims,
            "signature": base64.b64encode(
                self.private_key.sign(canonical(claims).encode())
            ).decode(),
        }


class AppGateway:
    def __init__(self, host, signer, *, servers, workspace_refs):
        self.host, self.signer, self.servers, self.workspace_refs = (
            host,
            signer,
            servers,
            workspace_refs,
        )

    @staticmethod
    def _automatic_confirmation(session):
        settings = {**session.get("settings", {}), **session.get("turn_settings", {})}
        if settings.get("permissions"):
            return None
        sandbox_policy = settings.get("sandboxPolicy") or {}
        sandbox = sandbox_policy.get("type") or settings.get("sandbox")
        approval = settings.get("approvalPolicy")
        reviewer = settings.get("approvalsReviewer", "user")
        if sandbox in ("dangerFullAccess", "danger-full-access") and approval == "never":
            return "full"
        if (sandbox in ("workspaceWrite", "workspace-write")
                and approval == "on-request"
                and reviewer in ("auto_review", "guardian_subagent")
                and not sandbox_policy.get("networkAccess")
                and not sandbox_policy.get("writableRoots")):
            return "auto"
        return None

    def _apply_permission_profile(self, row, session):
        if not row["write"] or row["state"] not in {"pending", "confirmed"}:
            return
        # An explicit user decision remains bound to these exact arguments.
        if row["state"] == "confirmed" and row.get("approval_source") != "permission_profile":
            return
        profile = self._automatic_confirmation(session)
        if profile:
            row.update(state="confirmed", approval_source="permission_profile",
                       permission_profile=profile,
                       interaction_id="interaction-auto-" + row["challenge_id"])
        else:
            row["state"] = "pending"
            for field in ("approval_source", "permission_profile", "interaction_id"):
                row.pop(field, None)

    def _scope(self, body):
        from .mcp_app_context import app_identity

        app_identity(body)
        server = self.servers.get(body["server"])
        if not server or body["uri"] not in server["resources"]:
            raise ValueError("unregistered MCP application")
        session = self.host._session_for_thread(body.get("threadId"))
        if not session or session["principal_id"] != self.host.principal_id:
            raise ValueError("application session unavailable")
        tool = body.get("name")
        if tool not in server["tools"]:
            raise ValueError("application tool not registered")
        cwd = session.get("options", {}).get(
            "cwd", session.get("cwd", str(self.host.workspace_dir))
        )
        resolved = Path(cwd).resolve()
        scopes = [(Path(root).resolve(), ref) for root, ref in self.workspace_refs.items()
                  if resolved.is_relative_to(Path(root).resolve())]
        if not scopes:
            raise ValueError("workspace is not registered for application calls")
        workspace = max(scopes, key=lambda scope: len(scope[0].parts))[1]
        return server, session, workspace

    async def automatic_call(self, body, service):
        """Authenticated services may request calls only for their own audience.

        No caller-supplied permission profile or confirmation is authoritative.
        The normal gateway owns signing, dispatch and durable replay protection.
        """
        server = self.servers.get(body.get("server"))
        if not server or server["audience"] != service:
            raise PermissionError("application service audience mismatch")
        session = self.host.get_session(body["host_session_id"])
        if not session:
            raise ValueError("application session unavailable")
        identity = {
            "server": body["server"],
            "uri": body["uri"],
            "instanceId": "service-" + service,
            "threadId": session["thread_id"],
            "name": body["name"],
            "arguments": body["arguments"],
        }
        _, scoped_session, _ = self._scope(identity)
        if scoped_session["id"] != session["id"]:
            raise ValueError("application session mismatch")
        if not self._automatic_confirmation(scoped_session):
            return {"state": "manual_required"}
        challenge = self.challenge(identity)
        if challenge["state"] == "denied":
            return {"state": "denied"}
        result = await self.call(challenge)
        return {"state": "unknown" if result.get("status") == "unknown" else "completed",
                "result": result}

    def challenge(self, body):
        server, session, workspace = self._scope(body)
        args = body.get("arguments") or {}
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        identity = {k: body[k] for k in ("server", "uri", "instanceId", "threadId", "name")}
        identity["arguments"] = args
        write = not server["tools"][body["name"]]["read_only"]
        key = "appcall-" + (arguments_digest(identity) if write else uuid.uuid4().hex)
        path = f"app-calls/{key}.json"
        previous = self.host.store.read(path)
        if previous:
            self._apply_permission_profile(previous, session)
            self.host.store.write(path, previous)
            return {
                k: previous[k] for k in ("call_id", "challenge_id", "state", "write", "expires_at")
            }
        row = dict(
            call_id=key,
            challenge_id=uuid.uuid4().hex,
            state="pending" if write else "confirmed",
            write=write,
            expires_at=time.time() + 120,
            identity=identity,
            session_id=session["id"],
            workspace_ref=workspace,
            principal_id=self.host.principal_id,
            audience=server["audience"],
        )
        self._apply_permission_profile(row, session)
        self.host.store.write(path, row)
        return {k: row[k] for k in ("call_id", "challenge_id", "state", "write", "expires_at")}

    def confirm(self, body):
        from .state import identifier

        path = f"app-calls/{identifier(body['call_id'])}.json"
        row = self.host.store.read(path)
        if (
            not row
            or row["principal_id"] != self.host.principal_id
            or row["challenge_id"] != body.get("challenge_id")
            or row["expires_at"] < time.time()
            or body.get("decision") not in ("confirmed", "denied")
        ):
            raise ValueError("invalid or expired application challenge")
        if row["state"] in ("completed", "unknown"):
            return {"state": row["state"]}
        row["state"] = body["decision"]
        row["approval_source"] = "user"
        row.pop("permission_profile", None)
        row["interaction_id"] = "interaction-" + row["challenge_id"]
        self.host.store.write(path, row)
        return {"state": row["state"]}

    async def call(self, body):
        from .state import identifier

        path = f"app-calls/{identifier(body['call_id'])}.json"
        row = self.host.store.read(path)
        if not row or row["principal_id"] != self.host.principal_id:
            raise ValueError("unknown application call")
        if row["state"] == "completed":
            return row["result"]
        if row["state"] in ("dispatching", "unknown"):
            raise ValueError("application call outcome unknown; reconcile original receipt")
        identity = row["identity"]
        server, session, workspace = self._scope(identity)
        self._apply_permission_profile(row, session)
        self.host.store.write(path, row)
        if row["state"] != "confirmed" or row["expires_at"] < time.time():
            raise ValueError("application confirmation required")
        proof = self.signer.sign_call(
            audience=row["audience"],
            session_id=session["id"],
            thread_id=session["thread_id"],
            workspace_ref=workspace,
            app_instance_id=identity["instanceId"],
            tool=identity["name"],
            arguments=identity["arguments"],
            call_id=row["call_id"],
            interaction=(
                {"id": row.get("interaction_id"), "decision": "confirmed",
                 "approval_source": row.get("approval_source", "user"),
                 **({"permission_profile": row["permission_profile"]}
                    if row.get("permission_profile") else {})} if row["write"] else None
            ),
        )
        # The app-supplied _meta is deliberately not copied across the trust boundary.
        row["state"] = "dispatching"
        self.host.store.write(path, row)
        try:
            result = await self.host.native.call(
                "mcpServer/tool/call",
                {
                    "threadId": session["thread_id"],
                    "server": identity["server"],
                    "tool": identity["name"],
                    "arguments": identity["arguments"],
                    "_meta": {"io.codex.host/call-proof": proof},
                },
                row["call_id"],
            )
            row["state"] = "unknown" if result.get("status") == "unknown" else "completed"
            row["result"] = result
            self.host.store.write(path, row)
            return result
        except BaseException:
            row["state"] = "unknown"
            self.host.store.write(path, row)
            raise
