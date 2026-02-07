async function updatePreview() {
  const templateSelect = document.getElementById("template_id");
  const contactSelect = document.getElementById("preview_contact");
  if (!templateSelect || !contactSelect) return;
  const response = await fetch(
    `/preview?template_id=${templateSelect.value}&contact_id=${contactSelect.value}`
  );
  const data = await response.json();
  const subjectEl = document.getElementById("preview_subject");
  const bodyEl = document.getElementById("preview_body");
  subjectEl.textContent = data.subject || "Subject";
  bodyEl.textContent = data.body || "";
}

document.addEventListener("DOMContentLoaded", () => {
  const templateSelect = document.getElementById("template_id");
  const contactSelect = document.getElementById("preview_contact");
  if (templateSelect && contactSelect) {
    templateSelect.addEventListener("change", updatePreview);
    contactSelect.addEventListener("change", updatePreview);
    updatePreview();
  }
});
