function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

export async function mountRefusalReview(root, request, complaintId, onSaved) {
  const path = `/complaints/${complaintId}/refusal-review`;
  root.innerHTML = '<h3>Commander refusal review</h3><p>Loading refusal details…</p>';
  try {
    let review = await request(path);
    function draw() {
      const status = review.commander_review?.status;
      root.innerHTML = `<h3>Commander refusal review</h3>
        <p><strong>Refusal reason:</strong> ${escapeHtml(review.refusal_reason)}</p>
        <p><strong>Charge officer notes:</strong> ${escapeHtml(review.officer_notes || 'No notes recorded')}</p>
        <p><strong>Commander review:</strong> ${escapeHtml(status || 'Awaiting acknowledgement')}</p>
        ${review.commander_review?.resolution_notes ? `<p>Resolution: ${escapeHtml(review.commander_review.resolution_notes)}</p>` : ''}
        <p><strong>NCC escalation:</strong> ${escapeHtml(review.ncc_escalation?.status || 'Not escalated to NCC')}</p>
        <div class="message" data-review-message role="status" hidden></div>
        <div class="actions">${!status || status === 'OPEN' ? '<button type="button" class="button button-secondary" data-review-action="ACKNOWLEDGE">Acknowledge refusal</button>' : ''}</div>
        ${!review.ncc_escalation ? '<div class="field"><label for="commander-escalation-reason">Reason for escalating to NCC</label><textarea id="commander-escalation-reason" rows="3" maxlength="20000"></textarea></div><button type="button" class="button button-primary" data-review-action="ESCALATE">Escalate to NCC</button>' : '<p>This refusal has already been forwarded to NCC. Its existing record is preserved.</p>'}`;
      root.querySelectorAll('[data-review-action]').forEach(button => {
        button.onclick = async () => {
          const reason = root.querySelector('textarea');
          if (button.dataset.reviewAction === 'ESCALATE' && !reason.value.trim()) {
            reason.setCustomValidity('Enter a reason for escalating to NCC.');
            reason.reportValidity(); reason.oninput = () => reason.setCustomValidity('');
            return;
          }
          root.querySelectorAll('button').forEach(node => { node.disabled = true; });
          const message = root.querySelector('[data-review-message]');
          try {
            review = await request(path, {method:'POST', body:{decision_id:review.decision_id,
              action:button.dataset.reviewAction, ...(button.dataset.reviewAction === 'ESCALATE' ? {reason:reason.value.trim()} : {})}});
            draw();
            const saved = root.querySelector('[data-review-message]');
            saved.hidden = false; saved.textContent = button.dataset.reviewAction === 'ESCALATE' ? 'Refusal escalated to NCC.' : 'Refusal acknowledged.';
            await onSaved();
          } catch (error) {
            message.hidden = false; message.textContent = error.message;
            root.querySelectorAll('button').forEach(node => { node.disabled = false; });
          }
        };
      });
    }
    draw();
  } catch (error) {
    root.textContent = `Commander refusal review unavailable: ${error.message}`;
  }
}
