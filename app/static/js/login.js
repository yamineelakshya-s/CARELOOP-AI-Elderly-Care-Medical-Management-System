function bindAuthForm(formId, statusId, buttonId, endpoint, getPayload) {
    const form = document.getElementById(formId);
    const status = document.getElementById(statusId);
    const button = document.getElementById(buttonId);
    let completed = false;

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.submitting === "true" || completed) return;
        if (!form.reportValidity()) return;

        if (formId === "join-form") {
            const email = document.getElementById("join-email").value.trim();
            const password = document.getElementById("join-password").value;
            if (Boolean(email) !== Boolean(password)) {
                status.textContent = "Enter both an email and password, or leave both blank.";
                status.hidden = false;
                return;
            }
        }

        form.dataset.submitting = "true";
        form.setAttribute("aria-busy", "true");
        button.disabled = true;
        button.textContent = "Connecting…";
        status.hidden = true;

        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(getPayload())
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                const detail = typeof result.detail === "string" ? result.detail : "";
                if (response.status === 401) {
                    status.textContent = detail || "Email or password is incorrect.";
                } else if (response.status === 409) {
                    status.textContent = detail || "That email is already registered. Sign in instead.";
                } else if (response.status === 422) {
                    status.textContent = detail || "Check the information and try again.";
                } else if (response.status === 429) {
                    status.textContent = "Too many attempts. Please wait a few minutes before trying again.";
                } else {
                    status.textContent = "We couldn't connect your account. Please try again.";
                }
                status.hidden = false;
                return;
            }

            completed = true;
            button.textContent = "Opening your dashboard…";
            window.location.replace("/");
        } catch {
            status.textContent = "CareLoop AI couldn't be reached. Check your connection and try again.";
            status.hidden = false;
        } finally {
            form.removeAttribute("aria-busy");
            if (!completed) {
                delete form.dataset.submitting;
                button.disabled = false;
                button.textContent = formId === "login-form" ? "Sign in" : "Join care circle";
            }
        }
    });
}

bindAuthForm(
    "login-form",
    "login-status",
    "login-submit",
    "/api/auth/login",
    () => ({
        email: document.getElementById("login-email").value.trim(),
        password: document.getElementById("login-password").value
    })
);

bindAuthForm(
    "join-form",
    "join-status",
    "join-submit",
    "/api/auth/join",
    () => {
        const payload = {
            join_code: document.getElementById("join-code").value.trim().toUpperCase()
        };
        const email = document.getElementById("join-email").value.trim();
        const password = document.getElementById("join-password").value;
        if (email && password) {
            payload.email = email;
            payload.password = password;
        }
        return payload;
    }
);

const query = new URLSearchParams(window.location.search);
if (query.get("expired") === "1") {
    const status = document.getElementById("login-status");
    status.textContent = "Your session expired. Sign in again to continue.";
    status.hidden = false;
}
