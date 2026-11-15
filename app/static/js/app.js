const navButtons = document.querySelectorAll("[data-view]");
const views = document.querySelectorAll(".view");
const profile = document.querySelector(".profile");
const profileName = document.getElementById("profile-name");
const profileRole = document.getElementById("profile-role");
const profileAvatar = document.getElementById("profile-avatar");
const heading = document.getElementById("page-heading");
const subtitle = document.getElementById("page-subtitle");
const statusMessage = document.getElementById("dashboard-status");
const inviteControls = document.getElementById("invite-controls");
const medicationForm = document.getElementById("medication-form");
const medicationFormStatus = document.getElementById("medication-form-status");
let medicationData = {medications: [], manageable_circles: []};
let appointmentData = {appointments: [], manageable_circles: []};
let relayData = {items: [], manageable_circles: []};
let approvalData = {requests: [], requestable_circles: []};
let notificationData = {notifications: []};
let timelineData = {events: [], manageable_circles: []};
let adherenceData = null;
const viewTitles = {
    overview: "Care overview",
    medications: "Medications",
    appointments: "Appointments",
    relay: "Caregiver Relay",
    approvals: "Elder approvals",
    timeline: "Care timeline",
    "ai-summary": "AI care summary",
    notifications: "Notifications"
};
let currentUser = {
    name: profileName.textContent,
    role: profile.dataset.role
};

function showStatus(message) {
    statusMessage.textContent = message;
    statusMessage.hidden = false;
}

function beginFormSubmission(form) {
    if (form.dataset.submitting === "true") return false;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    return true;
}

function endFormSubmission(form) {
    delete form.dataset.submitting;
    form.removeAttribute("aria-busy");
}

function updateProfile(user) {
    currentUser = user;
    profile.dataset.role = user.role;
    profile.dataset.isDemo = String(user.is_demo);
    document.querySelector(".profile-editor").hidden = user.is_demo;
    const credentialsSetup = document.getElementById("credentials-setup");
    credentialsSetup.hidden = user.is_demo || user.has_credentials;
    credentialsSetup.open = !credentialsSetup.hidden;
    profileName.textContent = user.name;
    profileRole.textContent = `${user.role[0].toUpperCase()}${user.role.slice(1)} account${user.is_demo ? " · Demo" : ""}`;
    profileAvatar.textContent = user.name.trim().charAt(0).toUpperCase();
    document.getElementById("account-mode").textContent = user.is_demo
        ? "DEMO ACCOUNT"
        : "ACCOUNT";
    document.getElementById("welcome-title").textContent = user.role === "elder"
        ? "Your care, connected at your pace."
        : user.role === "caregiver"
            ? "Supporting care, one step at a time."
            : "Stay connected to the care that matters.";
    subtitle.textContent = user.role === "elder"
        ? "Your care overview."
        : "Your authorized care-circle workspace.";
    inviteControls.hidden = user.role !== "elder" || user.is_demo;
    updateHeading();
}

function updateHeading() {
    const activeButton = document.querySelector(".nav-link.active");
    const view = activeButton ? activeButton.dataset.view : "overview";
    heading.textContent = view === "overview"
        ? `Welcome, ${currentUser.name}`
        : viewTitles[view] || "CareLoop AI";
    if (view !== "overview") subtitle.textContent = "Your CareLoop AI workspace.";
}

function showView(id) {
    views.forEach((view) => {
        view.hidden = view.id !== id;
    });
    navButtons.forEach((button) => {
        const active = button.dataset.view === id;
        button.classList.toggle("active", active);
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    });
    updateHeading();
}

function makeRecord(title, details, context) {
    const item = document.createElement("div");
    item.className = "record-item";

    const content = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = title;
    content.appendChild(name);

    if (details) {
        const detail = document.createElement("small");
        detail.textContent = details;
        content.appendChild(detail);
    }

    item.appendChild(content);
    if (context) {
        const scope = document.createElement("small");
        scope.className = "record-context";
        scope.textContent = context;
        item.appendChild(scope);
    }
    return item;
}

function renderList(elementId, records, emptyMessage, renderRecord) {
    const list = document.getElementById(elementId);
    list.replaceChildren();
    if (records.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = emptyMessage;
        list.appendChild(empty);
        return;
    }
    records.forEach((record) => list.appendChild(renderRecord(record)));
}

function formatDoseTime(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function localDateTimeToUTC(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

function medicationCard(record) {
    const card = document.createElement("article");
    card.className = "medication-card";

    const heading = document.createElement("div");
    heading.className = "medication-card-heading";
    const title = document.createElement("div");
    const name = document.createElement("h4");
    name.textContent = record.name;
    title.appendChild(name);
    const summary = document.createElement("p");
    summary.textContent = [record.dosage, record.frequency].filter(Boolean).join(" · ");
    title.appendChild(summary);
    heading.appendChild(title);

    const actions = document.createElement("div");
    actions.className = "medication-actions";
    if (record.can_manage) {
        const edit = document.createElement("button");
        edit.className = "text-link";
        edit.type = "button";
        edit.dataset.action = "edit-medication";
        edit.dataset.medicationId = record.id;
        edit.textContent = "Edit";
        actions.appendChild(edit);

        const remove = document.createElement("button");
        remove.className = "text-link danger-link";
        remove.type = "button";
        remove.dataset.action = "delete-medication";
        remove.dataset.medicationId = record.id;
        remove.textContent = "Delete";
        actions.appendChild(remove);
    }
    heading.appendChild(actions);
    card.appendChild(heading);

    const scope = document.createElement("p");
    scope.className = "medication-scope";
    scope.textContent = `Care circle: ${record.elder_name}`;
    card.appendChild(scope);

    const dates = record.end_date
        ? `${record.start_date || "Start date not recorded"} to ${record.end_date}`
        : `${record.start_date || "Start date not recorded"} onward`;
    const dateLine = document.createElement("p");
    dateLine.className = "medication-dates";
    dateLine.textContent = dates;
    card.appendChild(dateLine);

    if (record.instructions) {
        const instructions = document.createElement("p");
        instructions.className = "medication-instructions";
        instructions.textContent = record.instructions;
        card.appendChild(instructions);
    }

    const doseHeading = document.createElement("h5");
    doseHeading.textContent = "Dose schedule and history";
    card.appendChild(doseHeading);
    const doses = document.createElement("div");
    doses.className = "dose-history";
    if (!record.doses.length) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No dose history has been recorded.";
        doses.appendChild(empty);
    } else {
        record.doses.forEach((dose) => {
            const doseItem = document.createElement("div");
            doseItem.className = "dose-history-item";
            const doseSummary = document.createElement("div");
            doseSummary.className = "dose-history-summary";
            const doseTime = document.createElement("strong");
            doseTime.textContent = formatDoseTime(dose.scheduled_at);
            doseSummary.appendChild(doseTime);
            const badge = document.createElement("span");
            badge.className = `dose-status status-${dose.status.toLowerCase()}`;
            badge.textContent = dose.status;
            doseSummary.appendChild(badge);
            doseItem.appendChild(doseSummary);

            if (dose.history.length) {
                const history = document.createElement("ul");
                history.className = "dose-change-list";
                dose.history.forEach((change) => {
                    const event = document.createElement("li");
                    event.textContent = `${change.status} · ${new Date(change.changed_at * 1000).toLocaleString()}`;
                    history.appendChild(event);
                });
                doseItem.appendChild(history);
            }

            if (record.can_manage) {
                const statusForm = document.createElement("form");
                statusForm.className = "dose-status-form";
                const statusLabel = document.createElement("label");
                statusLabel.textContent = "Update status";
                const statusSelect = document.createElement("select");
                statusSelect.setAttribute("aria-label", `Update dose status for ${record.name}`);
                ["Pending", "Taken", "Missed"].forEach((value) => {
                    const option = document.createElement("option");
                    option.value = value;
                    option.textContent = value;
                    option.selected = value === dose.status;
                    statusSelect.appendChild(option);
                });
                statusLabel.appendChild(statusSelect);
                statusForm.appendChild(statusLabel);
                const update = document.createElement("button");
                update.type = "submit";
                update.className = "secondary-button";
                update.textContent = "Update";
                statusForm.appendChild(update);
                statusForm.addEventListener("submit", async (event) => {
                    event.preventDefault();
                    if (!beginFormSubmission(statusForm)) return;
                    update.disabled = true;
                    try {
                        const response = await fetch(
                            `/api/medications/${encodeURIComponent(record.id)}/doses/${encodeURIComponent(dose.id)}`,
                            {
                                method: "PATCH",
                                headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({status: statusSelect.value})
                            }
                        );
                        if (!response.ok) throw new Error("dose update failed");
                        await loadDashboard();
                    } catch {
                        showStatus("The dose status could not be updated. Check your access and try again.");
                        update.disabled = false;
                    } finally {
                        endFormSubmission(statusForm);
                    }
                });
                doseItem.appendChild(statusForm);
            }
            doses.appendChild(doseItem);
        });
    }
    card.appendChild(doses);

    if (record.can_manage) {
        const doseForm = document.createElement("form");
        doseForm.className = "dose-create-form";
        const scheduledLabel = document.createElement("label");
        scheduledLabel.textContent = "Schedule a dose";
        const scheduledAt = document.createElement("input");
        scheduledAt.type = "datetime-local";
        scheduledAt.required = true;
        scheduledAt.setAttribute("aria-label", `Schedule a dose for ${record.name}`);
        scheduledLabel.appendChild(scheduledAt);
        doseForm.appendChild(scheduledLabel);
        const statusLabel = document.createElement("label");
        statusLabel.textContent = "Status";
        const statusSelect = document.createElement("select");
        ["Pending", "Taken", "Missed"].forEach((value) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = value;
            statusSelect.appendChild(option);
        });
        statusLabel.appendChild(statusSelect);
        doseForm.appendChild(statusLabel);
        const addDose = document.createElement("button");
        addDose.type = "submit";
        addDose.className = "secondary-button";
        addDose.textContent = "Record dose";
        doseForm.appendChild(addDose);
        doseForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!beginFormSubmission(doseForm)) return;
            addDose.disabled = true;
            try {
                const response = await fetch(
                    `/api/medications/${encodeURIComponent(record.id)}/doses`,
                    {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            scheduled_at: localDateTimeToUTC(scheduledAt.value),
                            status: statusSelect.value
                        })
                    }
                );
                const result = await response.json().catch(() => ({}));
                if (!response.ok) {
                    showStatus(result.detail || "The dose could not be recorded.");
                    addDose.disabled = false;
                    return;
                }
                await loadDashboard();
            } catch {
                showStatus("The dose could not be recorded. Check your access and try again.");
                addDose.disabled = false;
            } finally {
                endFormSubmission(doseForm);
            }
        });
        card.appendChild(doseForm);
    }
    return card;
}

function renderMedicationRecords() {
    const list = document.getElementById("medication-list");
    list.replaceChildren();
    const circles = medicationData.manageable_circles;
    const addButton = document.getElementById("medication-add");
    const circleField = document.getElementById("medication-circle-field");
    const circleSelect = document.getElementById("medication-circle");
    addButton.hidden = circles.length === 0;
    circleField.hidden = circles.length < 2;
    circleSelect.replaceChildren();
    circles.forEach((circle) => {
        const option = document.createElement("option");
        option.value = circle.id;
        option.textContent = circle.elder_name;
        circleSelect.appendChild(option);
    });

    if (medicationData.medications.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No authorized medication records are available.";
        list.appendChild(empty);
        return;
    }
    medicationData.medications.forEach((record) => {
        list.appendChild(medicationCard(record));
    });
}

function fillCircleSelect(select, circles) {
    select.replaceChildren();
    circles.forEach((circle) => {
        const option = document.createElement("option");
        option.value = circle.id;
        option.textContent = circle.elder_name;
        select.appendChild(option);
    });
}

function actionButton(label, action, recordId, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `text-link ${extraClass}`.trim();
    button.dataset.action = action;
    button.dataset.recordId = recordId;
    button.textContent = label;
    return button;
}

function renderAppointments() {
    const list = document.getElementById("appointment-list");
    const circles = appointmentData.manageable_circles;
    document.getElementById("appointment-add").hidden = circles.length === 0;
    document.getElementById("appointment-circle-field").hidden = circles.length < 2;
    fillCircleSelect(document.getElementById("appointment-circle"), circles);
    list.replaceChildren();
    if (appointmentData.appointments.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No authorized appointments are available.";
        list.appendChild(empty);
        return;
    }
    appointmentData.appointments.forEach((record) => {
        const item = makeRecord(
            record.title,
            [formatDoseTime(record.starts_at), record.provider, record.location]
                .filter(Boolean).join(" · "),
            record.elder_name
        );
        if (record.can_manage) {
            const actions = document.createElement("div");
            actions.className = "feature-actions";
            actions.append(
                actionButton("Edit", "edit-appointment", record.id),
                actionButton("Delete", "delete-appointment", record.id, "danger-link")
            );
            item.appendChild(actions);
        }
        list.appendChild(item);
    });
}

function renderRelay() {
    const list = document.getElementById("relay-list");
    const circles = relayData.manageable_circles;
    document.getElementById("relay-add").hidden = circles.length === 0;
    document.getElementById("relay-circle-field").hidden = circles.length < 2;
    fillCircleSelect(document.getElementById("relay-circle"), circles);
    list.replaceChildren();
    if (relayData.items.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No relay follow-ups are available in your authorized circles.";
        list.appendChild(empty);
        return;
    }
    relayData.items.forEach((record) => {
        const item = makeRecord(
            record.title,
            [
                record.body,
                `Status: ${record.status}`,
                `Added by ${record.creator_name}`
            ]
                .filter(Boolean).join(" · "),
            [
                record.elder_name,
                record.assignee_name ? `Assigned to ${record.assignee_name}` : "",
                record.claimant_name ? `Handling: ${record.claimant_name}` : ""
            ].filter(Boolean).join(" · ")
        );
        if (record.can_manage) {
            const actions = document.createElement("div");
            actions.className = "feature-actions";
            if (record.medication_dose_id) {
                if (record.can_claim) {
                    actions.append(actionButton("Claim", "claim-dose-alert", record.id));
                } else if (
                    ["claimed", "contacted"].includes(record.status)
                    && record.claimed_by_user_id === currentUser.id
                ) {
                    if (record.status === "claimed") {
                        actions.append(
                            actionButton("Mark contacted", "contact-dose-alert", record.id)
                        );
                    }
                    actions.append(
                        actionButton("Resolve", "resolve-dose-alert", record.id)
                    );
                } else if (record.status === "resolved") {
                    actions.append(
                        actionButton("Reopen", "reopen-dose-alert", record.id)
                    );
                }
            } else {
                actions.append(
                    actionButton("Edit", "edit-relay", record.id),
                    actionButton(
                        record.status === "open" ? "Complete" : "Reopen",
                        "toggle-relay",
                        record.id
                    ),
                    actionButton("Delete", "delete-relay", record.id, "danger-link")
                );
            }
            if (actions.childElementCount > 0) item.appendChild(actions);
        }
        list.appendChild(item);
    });
}

function renderApprovals() {
    const list = document.getElementById("approval-list");
    const circles = approvalData.requestable_circles;
    const form = document.getElementById("approval-form");
    form.hidden = circles.length === 0;
    fillCircleSelect(document.getElementById("approval-circle"), circles);
    updateApprovalMedicationOptions();
    toggleApprovalMedicationFields();
    list.replaceChildren();
    if (approvalData.requests.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No approval requests are available.";
        list.appendChild(empty);
        return;
    }
    approvalData.requests.forEach((request) => {
        const proposal = request.proposed_changes;
        const proposalText = proposal
            ? [
                `Proposed medication: ${proposal.name}`,
                `Dosage: ${proposal.dosage}`,
                `Frequency: ${proposal.frequency}`,
                `Starts: ${proposal.start_date}`,
                proposal.end_date ? `Ends: ${proposal.end_date}` : "No end date",
                proposal.instructions
                    ? `Instructions: ${proposal.instructions}`
                    : ""
            ].filter(Boolean).join(" · ")
            : "";
        const item = makeRecord(
            request.title,
            [
                request.request_type,
                request.details,
                proposalText,
                `Status: ${request.status}`,
                request.decision_note
            ].filter(Boolean).join(" · "),
            `${request.elder_name} · Requested by ${request.requester_name}`
        );
        if (request.can_decide && request.status === "pending") {
            const actions = document.createElement("div");
            actions.className = "feature-actions";
            actions.append(
                actionButton("Approve", "approve-request", request.id),
                actionButton("Reject", "reject-request", request.id, "danger-link")
            );
            item.appendChild(actions);
        }
        list.appendChild(item);
    });
}

function updateApprovalMedicationOptions() {
    const select = document.getElementById("approval-medication");
    const previousValue = select.value;
    const circleId = document.getElementById("approval-circle").value;
    const records = medicationData.medications.filter(
        (record) => record.care_circle_id === circleId
    );
    select.replaceChildren();
    records.forEach((record) => {
        const option = document.createElement("option");
        option.value = record.id;
        option.textContent = `${record.name} (${record.dosage})`;
        select.appendChild(option);
    });
    if (records.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No authorized medication records";
        select.appendChild(option);
    } else if (records.some((record) => record.id === previousValue)) {
        select.value = previousValue;
    }
    fillMedicationProposalFields();
}

function fillMedicationProposalFields() {
    const record = medicationData.medications.find(
        (item) => item.id === document.getElementById("approval-medication").value
    );
    if (!record) return;
    document.getElementById("approval-medication-name").value = record.name;
    document.getElementById("approval-medication-dosage").value = record.dosage;
    document.getElementById("approval-medication-frequency").value = record.frequency;
    document.getElementById("approval-medication-start").value = record.start_date || "";
    document.getElementById("approval-medication-end").value = record.end_date || "";
    document.getElementById("approval-medication-instructions").value = record.instructions;
}

function toggleApprovalMedicationFields() {
    const visible = document.getElementById("approval-type").value === "medication";
    document.getElementById("approval-medication-fields").hidden = !visible;
    [
        "approval-medication",
        "approval-medication-name",
        "approval-medication-dosage",
        "approval-medication-frequency",
        "approval-medication-start"
    ].forEach((id) => {
        document.getElementById(id).required = visible;
    });
}

function renderNotifications() {
    const list = document.getElementById("notification-list");
    list.replaceChildren();
    const unread = notificationData.notifications.filter((item) => !item.read_at).length;
    const count = document.getElementById("notification-count");
    count.textContent = unread;
    count.hidden = unread === 0;
    if (notificationData.notifications.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "You have no notifications.";
        list.appendChild(empty);
        return;
    }
    notificationData.notifications.forEach((notification) => {
        const item = makeRecord(
            notification.title,
            `${notification.message} · ${formatDoseTime(notification.created_at * 1000)}`,
            notification.read_at ? "Read" : "Unread"
        );
        if (!notification.read_at) {
            const markRead = actionButton(
                "Mark as read", "read-notification", notification.id
            );
            item.appendChild(markRead);
        }
        list.appendChild(item);
    });
}

function renderTimeline() {
    const list = document.getElementById("activity-list");
    const circles = timelineData.manageable_circles;
    const form = document.getElementById("timeline-form");
    form.hidden = circles.length === 0;
    fillCircleSelect(document.getElementById("timeline-circle"), circles);
    list.replaceChildren();
    if (timelineData.events.length === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No care timeline updates are available.";
        list.appendChild(empty);
        return;
    }
    timelineData.events.forEach((event) => {
        list.appendChild(makeRecord(
            event.description,
            `${formatDoseTime(event.occurred_at)}${event.actor_name ? ` · ${event.actor_name}` : ""}`,
            event.elder_name
        ));
    });
}

function renderDashboard(data, medications, appointments, relay, approvals, notifications, timeline) {
    statusMessage.hidden = true;
    updateProfile(data.profile);
    const circles = data.care_circles;
    medicationData = medications;
    appointmentData = appointments;
    relayData = relay;
    approvalData = approvals;
    notificationData = notifications;
    timelineData = timeline;
    const medicationRecords = medications.medications.map((record) => ({
        ...record,
        circleName: record.elder_name
    }));
    const appointmentItems = appointments.appointments.map((record) => ({
        ...record,
        circleName: record.elder_name
    }));
    const activities = timeline.events.map((record) => ({
        ...record,
        circleName: record.elder_name
    }));

    document.getElementById("medication-count").textContent = medicationRecords.length;
    document.getElementById("appointment-count").textContent = appointmentItems.length;
    document.getElementById("circle-count").textContent = circles.length;
    document.getElementById("activity-count").textContent = activities.length;

    const medicationSummary = (record) => makeRecord(
        record.name,
        [record.dosage, record.frequency, record.instructions].filter(Boolean).join(" · "),
        record.circleName
    );
    const appointmentSummary = (record) => makeRecord(
        record.title,
        [formatDoseTime(record.starts_at), record.provider, record.location]
            .filter(Boolean).join(" · "),
        record.circleName
    );
    const activityRecords = (record) => makeRecord(
        record.description,
        formatDoseTime(record.occurred_at),
        record.circleName
    );

    renderList(
        "overview-medication-list",
        medicationRecords.slice(0, 3),
        "No authorized medication records are available.",
        medicationSummary
    );
    renderMedicationRecords();
    renderAppointments();
    renderRelay();
    renderApprovals();
    renderNotifications();
    renderTimeline();
    renderAdherence(adherenceData);
    renderList(
        "overview-appointment-list",
        appointmentItems.slice(0, 3),
        "No authorized appointment records are available.",
        appointmentSummary
    );
    renderList(
        "overview-activity-list",
        activities.slice(0, 3),
        "No authorized care activities are available.",
        activityRecords
    );
    document.getElementById("profile-name-input").value = data.profile.name;
    renderList(
        "care-circle-list",
        circles,
        "No care circles are linked to your account.",
        (circle) => makeRecord(
            circle.elder.name,
            `${circle.member_count} member${circle.member_count === 1 ? "" : "s"}`,
            circle.permissions.length
                ? `Access: ${circle.permissions.join(", ")}`
                : "No personal record access granted"
        )
    );
    document.getElementById("welcome-description").textContent = circles.length
        ? "Your dashboard includes only your own records and explicitly authorized care circles."
        : "No care-circle records are linked to your account yet.";
}

function localDateString(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function currentWeekStart() {
    const monday = new Date();
    monday.setHours(0, 0, 0, 0);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    return localDateString(monday);
}

function renderAdherence(data) {
    const summary = document.getElementById("adherence-summary");
    if (!data || !Array.isArray(data.care_circles)) {
        summary.textContent = "Weekly dose information could not be loaded.";
        return;
    }
    if (data.care_circles.length === 0) {
        summary.textContent = "No medication data is available for this account.";
        return;
    }
    if (data.scheduled === 0) {
        summary.textContent = (
            `No doses were scheduled for the week of ${data.week_start}; `
            + "adherence is not available."
        );
        return;
    }
    summary.textContent = (
        `${data.taken} of ${data.scheduled} scheduled doses were confirmed as taken`
        + ` — ${data.adherence_percent}% adherence. `
        + `Missed: ${data.missed}; pending/unconfirmed: ${data.pending}.`
    );
}

function openMedicationForm(record = null) {
    medicationForm.reset();
    medicationFormStatus.hidden = true;
    document.getElementById("medication-edit-id").value = record ? record.id : "";
    document.getElementById("medication-form-title").textContent = record
        ? "Edit medication"
        : "Add medication";
    document.getElementById("medication-save").textContent = record
        ? "Save changes"
        : "Save medication";
    document.getElementById("medication-circle-field").hidden = Boolean(record)
        || medicationData.manageable_circles.length < 2;
    if (record) {
        document.getElementById("medication-name").value = record.name;
        document.getElementById("medication-dosage").value = record.dosage;
        document.getElementById("medication-frequency").value = record.frequency;
        document.getElementById("medication-start-date").value = record.start_date;
        document.getElementById("medication-end-date").value = record.end_date || "";
        document.getElementById("medication-instructions").value = record.instructions;
    }
    medicationForm.hidden = false;
    document.getElementById("medication-name").focus();
}

document.getElementById("medication-add").addEventListener("click", () => {
    openMedicationForm();
});
document.getElementById("medication-cancel").addEventListener("click", () => {
    medicationForm.hidden = true;
});
medicationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!beginFormSubmission(medicationForm)) return;
    const medicationId = document.getElementById("medication-edit-id").value;
    const payload = {
        name: document.getElementById("medication-name").value.trim(),
        dosage: document.getElementById("medication-dosage").value.trim(),
        frequency: document.getElementById("medication-frequency").value.trim(),
        start_date: document.getElementById("medication-start-date").value,
        end_date: document.getElementById("medication-end-date").value || null,
        instructions: document.getElementById("medication-instructions").value.trim()
    };
    if (!medicationId) {
        payload.care_circle_id = document.getElementById("medication-circle").value;
    }
    const submit = document.getElementById("medication-save");
    submit.disabled = true;
    medicationFormStatus.hidden = true;
    try {
        const response = await fetch(
            medicationId
                ? `/api/medications/${encodeURIComponent(medicationId)}`
                : "/api/medications",
            {
                method: medicationId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            medicationFormStatus.textContent = result.detail || "The medication could not be saved.";
            medicationFormStatus.hidden = false;
            return;
        }
        medicationForm.hidden = true;
        await loadDashboard();
        showView("medications");
    } catch {
        medicationFormStatus.textContent = "CareLoop AI could not be reached. Try again.";
        medicationFormStatus.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(medicationForm);
    }
});

document.getElementById("medication-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const record = medicationData.medications.find(
        (item) => item.id === button.dataset.medicationId
    );
    if (!record || !record.can_manage) return;
    if (button.dataset.action === "edit-medication") {
        openMedicationForm(record);
        return;
    }
    if (button.dataset.action === "delete-medication") {
        if (!window.confirm(`Delete ${record.name} and its dose history?`)) return;
        button.disabled = true;
        try {
            const response = await fetch(
                `/api/medications/${encodeURIComponent(record.id)}`,
                {method: "DELETE"}
            );
            if (!response.ok) throw new Error("medication delete failed");
            await loadDashboard();
            showView("medications");
        } catch {
            showStatus("The medication could not be deleted. Check your access and try again.");
            button.disabled = false;
        }
    }
});

function dateTimeInputValue(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "";
    return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16);
}

function openAppointmentForm(record = null) {
    const form = document.getElementById("appointment-form");
    form.reset();
    const circles = appointmentData.manageable_circles;
    document.getElementById("appointment-edit-id").value = record ? record.id : "";
    document.getElementById("appointment-form-title").textContent = record
        ? "Edit appointment"
        : "Add appointment";
    document.getElementById("appointment-save").textContent = record
        ? "Save changes"
        : "Save appointment";
    document.getElementById("appointment-circle-field").hidden = Boolean(record)
        || circles.length < 2;
    if (record) {
        document.getElementById("appointment-title").value = record.title;
        document.getElementById("appointment-starts").value = dateTimeInputValue(record.starts_at);
        document.getElementById("appointment-provider").value = record.provider;
        document.getElementById("appointment-location").value = record.location;
    }
    document.getElementById("appointment-form-status").hidden = true;
    form.hidden = false;
    document.getElementById("appointment-title").focus();
}

document.getElementById("appointment-add").addEventListener("click", () => {
    openAppointmentForm();
});
document.getElementById("appointment-cancel").addEventListener("click", () => {
    document.getElementById("appointment-form").hidden = true;
});
const appointmentForm = document.getElementById("appointment-form");
appointmentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!beginFormSubmission(appointmentForm)) return;
    const appointmentId = document.getElementById("appointment-edit-id").value;
    const payload = {
        title: document.getElementById("appointment-title").value.trim(),
        starts_at: localDateTimeToUTC(
            document.getElementById("appointment-starts").value
        ),
        provider: document.getElementById("appointment-provider").value.trim(),
        location: document.getElementById("appointment-location").value.trim()
    };
    if (!appointmentId) {
        payload.care_circle_id = document.getElementById("appointment-circle").value;
    }
    const submit = document.getElementById("appointment-save");
    const status = document.getElementById("appointment-form-status");
    submit.disabled = true;
    status.hidden = true;
    try {
        const response = await fetch(
            appointmentId
                ? `/api/appointments/${encodeURIComponent(appointmentId)}`
                : "/api/appointments",
            {
                method: appointmentId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "The appointment could not be saved.";
            status.hidden = false;
            return;
        }
        document.getElementById("appointment-form").hidden = true;
        await loadDashboard();
        showView("appointments");
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(appointmentForm);
    }
});

document.getElementById("appointment-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const appointment = appointmentData.appointments.find(
        (item) => item.id === button.dataset.recordId
    );
    if (!appointment || !appointment.can_manage) return;
    if (button.dataset.action === "edit-appointment") {
        openAppointmentForm(appointment);
        return;
    }
    if (button.dataset.action === "delete-appointment") {
        if (!window.confirm(`Delete ${appointment.title}?`)) return;
        button.disabled = true;
        try {
            const response = await fetch(
                `/api/appointments/${encodeURIComponent(appointment.id)}`,
                {method: "DELETE"}
            );
            if (!response.ok) throw new Error("appointment delete failed");
            await loadDashboard();
            showView("appointments");
        } catch {
            showStatus("The appointment could not be deleted. Check your access and try again.");
            button.disabled = false;
        }
    }
});

function fillRelayAssignees(circleId, selectedId = "") {
    const select = document.getElementById("relay-assignee");
    select.replaceChildren();
    const unassigned = document.createElement("option");
    unassigned.value = "";
    unassigned.textContent = "Unassigned";
    select.appendChild(unassigned);
    const circle = relayData.manageable_circles.find((item) => item.id === circleId);
    (circle ? circle.members : []).forEach((member) => {
        const option = document.createElement("option");
        option.value = member.id;
        option.textContent = `${member.name} (${member.role})`;
        option.selected = member.id === selectedId;
        select.appendChild(option);
    });
}

function openRelayForm(record = null) {
    const form = document.getElementById("relay-form");
    const circles = relayData.manageable_circles;
    form.reset();
    document.getElementById("relay-edit-id").value = record ? record.id : "";
    document.getElementById("relay-form-title").textContent = record
        ? "Edit follow-up"
        : "Add follow-up";
    document.getElementById("relay-save").textContent = record
        ? "Save changes"
        : "Save follow-up";
    document.getElementById("relay-circle-field").hidden = Boolean(record)
        || circles.length < 2;
    if (record) {
        document.getElementById("relay-title").value = record.title;
        document.getElementById("relay-body").value = record.body;
        document.getElementById("relay-status").value = record.status;
    }
    const selectedCircle = record
        ? record.care_circle_id
        : document.getElementById("relay-circle").value;
    fillRelayAssignees(selectedCircle, record ? record.assigned_to_user_id : "");
    document.getElementById("relay-status-field").hidden = !record;
    document.getElementById("relay-status").hidden = !record;
    document.getElementById("relay-form-status").hidden = true;
    form.hidden = false;
    document.getElementById("relay-title").focus();
}

document.getElementById("relay-add").addEventListener("click", () => {
    openRelayForm();
});
document.getElementById("relay-cancel").addEventListener("click", () => {
    document.getElementById("relay-form").hidden = true;
});
document.getElementById("relay-circle").addEventListener("change", (event) => {
    fillRelayAssignees(event.target.value);
});
document.getElementById("relay-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!beginFormSubmission(form)) return;
    const relayId = document.getElementById("relay-edit-id").value;
    const payload = {
        title: document.getElementById("relay-title").value.trim(),
        body: document.getElementById("relay-body").value.trim(),
        assigned_to_user_id: document.getElementById("relay-assignee").value || null
    };
    if (relayId) {
        payload.status = document.getElementById("relay-status").value;
    } else {
        payload.care_circle_id = document.getElementById("relay-circle").value;
    }
    const submit = document.getElementById("relay-save");
    const status = document.getElementById("relay-form-status");
    submit.disabled = true;
    status.hidden = true;
    try {
        const response = await fetch(
            relayId ? `/api/relay/${encodeURIComponent(relayId)}` : "/api/relay",
            {
                method: relayId ? "PUT" : "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload)
            }
        );
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "The follow-up could not be saved.";
            status.hidden = false;
            return;
        }
        document.getElementById("relay-form").hidden = true;
        await loadDashboard();
        showView("relay");
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(form);
    }
});

document.getElementById("relay-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = relayData.items.find(
        (record) => record.id === button.dataset.recordId
    );
    if (!item || !item.can_manage) return;
    const alertActions = {
        "claim-dose-alert": "claim",
        "contact-dose-alert": "contact",
        "resolve-dose-alert": "resolve",
        "reopen-dose-alert": "reopen"
    };
    if (alertActions[button.dataset.action]) {
        button.disabled = true;
        try {
            const response = await fetch(
                `/api/relay/${encodeURIComponent(item.id)}/${alertActions[button.dataset.action]}`,
                {method: "POST"}
            );
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                showStatus(result.detail || "The dose follow-up could not be updated.");
                button.disabled = false;
                return;
            }
            await loadDashboard();
            showView("relay");
        } catch {
            showStatus("The dose follow-up could not be updated. Check your access and try again.");
            button.disabled = false;
        }
        return;
    }
    if (button.dataset.action === "edit-relay") {
        openRelayForm(item);
        return;
    }
    if (button.dataset.action === "delete-relay") {
        if (!window.confirm(`Delete ${item.title}?`)) return;
        button.disabled = true;
        try {
            const response = await fetch(
                `/api/relay/${encodeURIComponent(item.id)}`,
                {method: "DELETE"}
            );
            if (!response.ok) throw new Error("relay delete failed");
            await loadDashboard();
            showView("relay");
        } catch {
            showStatus("The relay item could not be deleted. Check your access and try again.");
            button.disabled = false;
        }
        return;
    }
    if (button.dataset.action === "toggle-relay") {
        button.disabled = true;
        try {
            const response = await fetch(
                `/api/relay/${encodeURIComponent(item.id)}`,
                {
                    method: "PUT",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        title: item.title,
                        body: item.body,
                        assigned_to_user_id: item.assigned_to_user_id,
                        status: item.status === "open" ? "completed" : "open"
                    })
                }
            );
            if (!response.ok) throw new Error("relay update failed");
            await loadDashboard();
            showView("relay");
        } catch {
            showStatus("The relay item could not be updated. Check your access and try again.");
            button.disabled = false;
        }
    }
});

const approvalForm = document.getElementById("approval-form");
document.getElementById("approval-type").addEventListener(
    "change",
    toggleApprovalMedicationFields
);
document.getElementById("approval-circle").addEventListener(
    "change",
    updateApprovalMedicationOptions
);
document.getElementById("approval-medication").addEventListener(
    "change",
    fillMedicationProposalFields
);
approvalForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!beginFormSubmission(form)) return;
    const status = document.getElementById("approval-form-status");
    const submit = form.querySelector('button[type="submit"]');
    status.hidden = true;
    submit.disabled = true;
    try {
        const payload = {
            care_circle_id: document.getElementById("approval-circle").value,
            request_type: document.getElementById("approval-type").value,
            title: document.getElementById("approval-title").value.trim(),
            details: document.getElementById("approval-details").value.trim()
        };
        if (payload.request_type === "medication") {
            payload.medication_id = document.getElementById("approval-medication").value;
            payload.proposed_changes = {
                name: document.getElementById("approval-medication-name").value.trim(),
                dosage: document.getElementById("approval-medication-dosage").value.trim(),
                frequency: document.getElementById("approval-medication-frequency").value.trim(),
                start_date: document.getElementById("approval-medication-start").value,
                end_date: document.getElementById("approval-medication-end").value || null,
                instructions: document.getElementById("approval-medication-instructions").value.trim()
            };
        }
        const response = await fetch("/api/approvals", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "The approval request could not be submitted.";
            status.hidden = false;
            return;
        }
        form.reset();
        await loadDashboard();
        showView("approvals");
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(form);
    }
});

document.getElementById("approval-list").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const request = approvalData.requests.find(
        (item) => item.id === button.dataset.recordId
    );
    if (!request || !request.can_decide || request.status !== "pending") return;
    const decision = button.dataset.action === "approve-request"
        ? "approved"
        : "rejected";
    const confirmation = decision === "approved"
        ? "Approve this request? Any proposed medication changes will be applied."
        : "Reject this request? This decision will be saved.";
    if (!window.confirm(confirmation)) return;
    button.disabled = true;
    try {
        const response = await fetch(
            `/api/approvals/${encodeURIComponent(request.id)}/decision`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({status: decision, decision_note: ""})
            }
        );
        if (!response.ok) throw new Error("approval decision failed");
        await loadDashboard();
        showView("approvals");
    } catch {
        showStatus("The approval decision could not be saved. Check your access and try again.");
        button.disabled = false;
    }
});

const timelineForm = document.getElementById("timeline-form");
timelineForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!beginFormSubmission(form)) return;
    const status = document.getElementById("timeline-form-status");
    const submit = form.querySelector('button[type="submit"]');
    status.hidden = true;
    submit.disabled = true;
    try {
        const response = await fetch("/api/timeline", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                care_circle_id: document.getElementById("timeline-circle").value,
                description: document.getElementById("timeline-description").value.trim()
            })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "The update could not be recorded.";
            status.hidden = false;
            return;
        }
        form.reset();
        await loadDashboard();
        showView("timeline");
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(form);
    }
});

document.getElementById("notification-list").addEventListener("click", async (event) => {
    const button = event.target.closest('button[data-action="read-notification"]');
    if (!button) return;
    button.disabled = true;
    try {
        const response = await fetch(
            `/api/notifications/${encodeURIComponent(button.dataset.recordId)}/read`,
            {method: "POST"}
        );
        if (!response.ok) throw new Error("notification update failed");
        await loadDashboard();
        showView("notifications");
    } catch {
        showStatus("The notification could not be marked as read.");
        button.disabled = false;
    }
});

const profileForm = document.getElementById("profile-form");
profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!beginFormSubmission(profileForm)) return;
    const status = document.getElementById("profile-status");
    const submit = profileForm.querySelector('button[type="submit"]');
    status.hidden = true;
    submit.disabled = true;
    try {
        const response = await fetch("/api/profile", {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name: document.getElementById("profile-name-input").value.trim()
            })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "Your profile could not be updated.";
            status.hidden = false;
            return;
        }
        await loadDashboard();
        status.textContent = "Your profile was updated.";
        status.hidden = false;
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(profileForm);
    }
});

const credentialsForm = document.getElementById("credentials-form");
credentialsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!beginFormSubmission(credentialsForm)) return;
    const status = document.getElementById("credentials-status");
    const submit = credentialsForm.querySelector('button[type="submit"]');
    status.hidden = true;
    submit.disabled = true;
    const password = document.getElementById("credentials-password").value;
    if (password !== document.getElementById("credentials-password-confirm").value) {
        status.textContent = "The passwords do not match.";
        status.hidden = false;
        submit.disabled = false;
        endFormSubmission(credentialsForm);
        return;
    }
    try {
        const response = await fetch("/api/auth/credentials", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                email: document.getElementById("credentials-email").value.trim(),
                password
            })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "Sign-in details could not be saved.";
            status.hidden = false;
            return;
        }
        credentialsForm.reset();
        updateProfile({...currentUser, has_credentials: true});
        showStatus("Sign-in details saved. You can now sign in again after logging out.");
    } catch {
        status.textContent = "CareLoop AI could not be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(credentialsForm);
    }
});

document.getElementById("ai-summary-load").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const result = document.getElementById("ai-summary-result");
    result.replaceChildren();
    button.disabled = true;
    button.textContent = "Preparing summary…";
    try {
        const response = await fetch("/api/ai/summary");
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || "The care summary could not be generated.");
        }
        const localSummary = document.createElement("p");
        localSummary.textContent = payload.summary;
        result.appendChild(localSummary);
        if (payload.general_guidance) {
            const guidance = document.createElement("p");
            guidance.textContent = payload.general_guidance;
            result.appendChild(guidance);
        } else {
            const unavailable = document.createElement("p");
            unavailable.className = "muted";
            unavailable.textContent = "Gemini is not configured; your local, account-scoped summary is available.";
            result.appendChild(unavailable);
        }
    } catch (error) {
        const message = document.createElement("p");
        message.className = "form-status";
        message.textContent = error.message || "The care summary could not be generated.";
        result.appendChild(message);
    } finally {
        button.disabled = false;
        button.textContent = "Refresh summary";
    }
});

async function loadDashboard() {
    try {
        const profileResponse = await fetch("/api/auth/me");
        if (profileResponse.status === 401) {
            window.location.replace("/login?expired=1");
            return;
        }
        if (!profileResponse.ok) throw new Error("profile request failed");
        const {user} = await profileResponse.json();
        if (!user || !user.id || !user.name || !user.role) {
            throw new Error("profile response was incomplete");
        }

        const dashboardResponse = await fetch("/api/dashboard");
        if (dashboardResponse.status === 401) {
            window.location.replace("/login?expired=1");
            return;
        }
        if (!dashboardResponse.ok) throw new Error("dashboard request failed");
        const data = await dashboardResponse.json();
        if (!data.profile || data.profile.id !== user.id || !Array.isArray(data.care_circles)) {
            throw new Error("dashboard response did not match the signed-in profile");
        }
        data.profile = {...data.profile, has_credentials: user.has_credentials};
        const adherenceWeek = document.getElementById("adherence-week");
        if (!adherenceWeek.value) adherenceWeek.value = currentWeekStart();
        const apiResponses = await Promise.all([
            fetch("/api/medications"),
            fetch("/api/appointments"),
            fetch("/api/relay"),
            fetch("/api/approvals"),
            fetch("/api/notifications"),
            fetch("/api/timeline"),
            fetch(
                `/api/medications/adherence?week_start=${encodeURIComponent(adherenceWeek.value)}`
            )
        ]);
        if (apiResponses.some((response) => response.status === 401)) {
            window.location.replace("/login?expired=1");
            return;
        }
        if (apiResponses.some((response) => !response.ok)) {
            throw new Error("dashboard feature request failed");
        }
        const [
            medications,
            appointments,
            relay,
            approvals,
            notifications,
            timeline,
            adherence
        ] = await Promise.all(apiResponses.map((response) => response.json()));
        if (
            !Array.isArray(medications.medications)
            || !Array.isArray(medications.manageable_circles)
            || !Array.isArray(appointments.appointments)
            || !Array.isArray(appointments.manageable_circles)
            || !Array.isArray(relay.items)
            || !Array.isArray(relay.manageable_circles)
            || !Array.isArray(approvals.requests)
            || !Array.isArray(approvals.requestable_circles)
            || !Array.isArray(notifications.notifications)
            || !Array.isArray(timeline.events)
            || !Array.isArray(timeline.manageable_circles)
            || !Array.isArray(adherence.care_circles)
        ) {
            throw new Error("dashboard feature response was incomplete");
        }
        adherenceData = adherence;
        renderDashboard(
            data, medications, appointments, relay,
            approvals, notifications, timeline
        );
    } catch {
        showStatus("We couldn't load your authorized care information. Your personal records are not shown; refresh to try again.");
        document.getElementById("welcome-description").textContent =
            "Your account information could not be loaded.";
    }
}

document.getElementById("adherence-week").addEventListener("change", () => {
    loadDashboard();
});

navButtons.forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
});
document.querySelectorAll("[data-open]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.open));
});

document.getElementById("logout-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (
        !currentUser.is_demo
        && !currentUser.has_credentials
        && !window.confirm(
            "This account does not have sign-in details yet. You will need a new invitation to return after logging out. Log out anyway?"
        )
    ) {
        return;
    }
    button.disabled = true;
    button.textContent = "Logging out…";
    try {
        const response = await fetch("/api/auth/logout", {method: "POST"});
        if (!response.ok) throw new Error("logout request failed");
        window.location.assign("/login");
    } catch {
        showStatus("We couldn't log you out. Check your connection and try again.");
        button.disabled = false;
        button.textContent = "Log out";
    }
});

let sessionCheckInProgress = false;
async function verifyActiveSession() {
    if (
        document.visibilityState !== "visible"
        || !currentUser.id
        || sessionCheckInProgress
    ) return;

    sessionCheckInProgress = true;
    try {
        const response = await fetch("/api/auth/me", {cache: "no-store"});
        if (response.status === 401) {
            window.location.replace("/login?expired=1");
            return;
        }
        if (!response.ok) throw new Error("session verification failed");
        const {user} = await response.json();
        if (!user || user.id !== currentUser.id) {
            window.location.reload();
        }
    } catch {
        showStatus("We couldn't verify this session. Refresh before continuing.");
    } finally {
        sessionCheckInProgress = false;
    }
}

window.addEventListener("focus", verifyActiveSession);
document.addEventListener("visibilitychange", verifyActiveSession);

const inviteOpen = document.getElementById("invite-open");
const inviteForm = document.getElementById("invite-form");
inviteOpen.addEventListener("click", () => {
    inviteForm.hidden = !inviteForm.hidden;
    if (!inviteForm.hidden) document.getElementById("invite-name").focus();
});
inviteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!beginFormSubmission(inviteForm)) return;
    const status = document.getElementById("invite-status");
    const permissions = Array.from(
        inviteForm.querySelectorAll('input[name="permissions"]:checked')
    ).map((input) => input.value);
    const submit = inviteForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    status.hidden = true;
    try {
        const response = await fetch("/api/auth/invitations", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                name: document.getElementById("invite-name").value.trim(),
                role: document.getElementById("invite-role").value,
                permissions
            })
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            status.textContent = result.detail || "The invitation could not be created.";
        } else {
            status.textContent = `Share this one-time code with the invitee: ${result.join_code}`;
            inviteForm.reset();
        }
        status.hidden = false;
    } catch {
        status.textContent = "CareLoop AI couldn't be reached. Try again.";
        status.hidden = false;
    } finally {
        submit.disabled = false;
        endFormSubmission(inviteForm);
    }
});

document.getElementById("current-date").textContent = new Date().toLocaleDateString(
    undefined,
    {weekday: "long", year: "numeric", month: "long", day: "numeric"}
).toUpperCase();

loadDashboard();
