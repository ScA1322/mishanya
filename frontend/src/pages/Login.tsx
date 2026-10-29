import { useState } from "react";
import { errorText, post } from "../api";

export default function Login({ onLogin }: { onLogin: (csrf: string) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ csrf_token: string }>("/api/auth/login", { password });
      onLogin(res.csrf_token);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="card login-card stack" onSubmit={submit}>
        <div className="brand" style={{ padding: 0 }}><div className="brand-logo">M</div>Mishanya</div>
        <div>
          <h1 style={{ fontSize: 18 }}>Вход в панель</h1>
          <p className="muted small">Пароль задан в файле <code>.env</code> (PANEL_PASSWORD).</p>
        </div>
        <label className="field">
          <span className="field-label">Пароль</span>
          <input className="input" type="password" autoFocus autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="alert alert-err">{error}</div>}
        <button className="btn btn-primary" disabled={busy || !password}>{busy ? "Проверка…" : "Войти"}</button>
      </form>
    </div>
  );
}
