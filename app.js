const $ = (selector) => document.querySelector(selector);
const fmt = (pesewas = 0) => `GH₵${(Number(pesewas) / 100).toFixed(2)}`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const productIcons = {
  'electrical-gadgets': '⌁', assets: '◇', stock: '▥',
  'mobile-gadgets': '▣', 'social-media-monetization': '◎',
};
let csrfToken = '';
let currentUser = null;
let dashboardData = null;
let isSignup = false;
let toastTimer;

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrfToken;
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const data = await response.json();
  if (data.csrf_token) csrfToken = data.csrf_token;
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}

function notify(message, error = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.remove('hidden', 'error');
  if (error) toast.classList.add('error');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add('hidden'), 4000);
}

function setMode(signup) {
  isSignup = signup;
  $('#authTitle').textContent = signup ? 'Create your account' : 'Welcome back';
  $('#authSub').textContent = signup ? 'Register your investIT account.' : 'Sign in to your investIT account.';
  $('#nameField').classList.toggle('hidden', !signup);
  $('#confirmField').classList.toggle('hidden', !signup);
  $('#authSubmit').textContent = signup ? 'Create account' : 'Sign in';
  $('#switchText').textContent = signup ? 'Already registered?' : 'New to investIT?';
  $('#switchMode').textContent = signup ? 'Sign in' : 'Create account';
  $('#password').autocomplete = signup ? 'new-password' : 'current-password';
  $('#password').minLength = signup ? 12 : 1;
  $('#authError').textContent = '';
}

function showAuth() {
  currentUser = null;
  dashboardData = null;
  $('#appView').classList.add('hidden');
  $('#authView').classList.remove('hidden');
  $('#password').value = '';
  setMode(false);
}

function showApp(page = 'overview') {
  const user = currentUser;
  if (!user) return;
  $('#authView').classList.add('hidden');
  $('#appView').classList.remove('hidden');
  $('#welcomeName').textContent = user.name.trim().split(/\s+/)[0];
  $('#sideName').textContent = user.name;
  $('#sidePhone').textContent = `+233 ${user.phone.slice(3)}`;
  $('#avatar').textContent = user.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase();
  const admin = user.role === 'admin';
  $('#rolePill').textContent = admin ? 'ADMINISTRATOR' : 'INVESTOR';
  $('#adminNav').classList.toggle('hidden', !admin);
  $('#adminNotice').classList.toggle('hidden', !admin);
  $('#today').textContent = new Intl.DateTimeFormat('en-GH', {
    weekday: 'short', day: 'numeric', month: 'short',
  }).format(new Date());
  go(page);
}

function productCard(product) {
  return `<article class="product"><div class="product-top"><span class="product-icon">${productIcons[product.id] || '◈'}</span><span class="term-tag">${product.duration_days} DAYS</span></div><h3>${esc(product.name)}</h3><p>Daily dividend · subject to product terms</p><div class="product-bottom"><div><div class="product-price">${fmt(product.price_pesewas)} <small>ENTRY</small></div><div class="daily">${fmt(product.daily_pesewas)} / day</div></div><button class="buy-btn" data-buy="${esc(product.id)}">Invest</button></div></article>`;
}

function activityRow(transaction) {
  const type = transaction.type;
  const icon = type === 'deposit' ? '↓' : type === 'withdrawal' ? '↑' : type === 'purchase' ? '◈' : type === 'credit' ? '＋' : type === 'debit' ? '−' : '↗';
  const status = transaction.status;
  const positive = type === 'dividend' || type === 'credit' || (type === 'deposit' && status === 'approved') || (type === 'withdrawal' && status === 'rejected');
  const negative = type === 'purchase' || type === 'debit' || type === 'withdrawal' || type === 'deposit';
  const amount = type === 'deposit' && status !== 'approved' ? fmt(transaction.amount_pesewas)
    : type === 'withdrawal' && status === 'rejected' ? `+${fmt(transaction.amount_pesewas)}`
      : `${positive ? '+' : negative ? '−' : '+'}${fmt(transaction.amount_pesewas)}`;
  const tone = positive ? 'positive' : negative ? 'negative' : 'pending';
  return `<div class="activity-row"><div class="activity-icon">${icon}</div><div class="activity-copy"><strong>${esc(transaction.description)}</strong><small>${new Date(transaction.created_at * 1000).toLocaleString('en-GH', { dateStyle: 'medium', timeStyle: 'short' })} · <span class="status ${esc(status)}">${esc(status)}</span></small></div><div class="activity-amount ${tone}">${amount}</div></div>`;
}

function renderDashboard(data) {
  const page = $('.page-section.active')?.id.replace('page-', '') || 'overview';
  dashboardData = data;
  currentUser = data.user;
  const now = Date.now() / 1000;
  const active = data.investments.filter((investment) => now < investment.started_at + investment.duration_days * 86400);
  const invested = active.reduce((sum, investment) => sum + investment.price_pesewas, 0);
  const earnings = data.investments.reduce((sum, investment) => sum + investment.earned_pesewas, 0);
  $('#balanceValue').textContent = fmt(data.user.balance_pesewas);
  $('#activeValue').textContent = fmt(invested);
  $('#activeCount').textContent = `${active.length} active ${active.length === 1 ? 'position' : 'positions'}`;
  $('#earningsValue').textContent = fmt(earnings);
  $('#featuredProducts').innerHTML = data.products.slice(0, 4).map(productCard).join('');
  $('#allProducts').innerHTML = data.products.map(productCard).join('');
  $('#recentActivity').innerHTML = data.transactions.slice(0, 5).map(activityRow).join('') || '<div class="empty">No account activity yet.</div>';
  $('#fullActivity').innerHTML = data.transactions.map(activityRow).join('') || '<div class="empty">No transactions yet.</div>';
  $('#userInvestments').innerHTML = data.investments.map((investment) => {
    const remaining = Math.max(0, investment.duration_days - Math.floor((now - investment.started_at) / 86400));
    return `<div class="activity-row"><div class="activity-icon">◈</div><div class="activity-copy"><strong>${esc(investment.product_name)}</strong><small>Started ${new Date(investment.started_at * 1000).toLocaleDateString('en-GH')} · ${remaining} days remaining</small></div><div class="activity-amount">${fmt(investment.price_pesewas)}</div></div>`;
  }).join('') || '<div class="empty">No active investments.</div>';
  $('#userMessages').innerHTML = data.messages.map((message) => `<div class="message-note"><strong>${new Date(message.created_at * 1000).toLocaleDateString('en-GH')}</strong><br>${esc(message.message)}</div>`).join('');
  $('#messagesPanel').classList.toggle('hidden', data.messages.length === 0);
  showApp(page);
}

async function refreshDashboard() {
  const data = await api('/api/dashboard');
  renderDashboard(data);
}

function go(page) {
  if (page === 'admin' && currentUser?.role !== 'admin') page = 'overview';
  document.querySelectorAll('.page-section').forEach((section) => section.classList.remove('active'));
  $(`#page-${page}`).classList.add('active');
  document.querySelectorAll('#mainNav button').forEach((button) => button.classList.toggle('active', button.dataset.page === page));
  $('#pageTitle').textContent = { overview: 'Overview', invest: 'Invest', activity: 'Activity', admin: 'Admin workspace' }[page];
  if (page === 'admin') refreshAdmin().catch((error) => notify(error.message, true));
}

function modal(title, content, onSubmit) {
  const root = $('#modalRoot');
  root.innerHTML = `<div class="modal-backdrop" id="backdrop"><div class="modal" role="dialog" aria-modal="true"><div class="modal-head"><h2>${title}</h2><button type="button" class="close-btn" aria-label="Close">×</button></div>${content}</div></div>`;
  root.querySelector('.close-btn').onclick = () => { root.innerHTML = ''; };
  root.querySelector('#backdrop').addEventListener('click', (event) => {
    if (event.target.id === 'backdrop') root.innerHTML = '';
  });
  const form = root.querySelector('form');
  if (form) form.addEventListener('submit', (event) => {
    event.preventDefault();
    onSubmit(new FormData(form), root);
  });
  root.querySelector('input')?.focus();
}

async function submitModal(root, path, body, successMessage) {
  try {
    await api(path, { method: 'POST', body: JSON.stringify(body) });
    root.innerHTML = '';
    await refreshDashboard();
    notify(successMessage);
  } catch (error) {
    notify(error.message, true);
  }
}

async function purchase(productId) {
  const product = dashboardData?.products.find((item) => item.id === productId);
  if (!product) return;
  if (!confirm(`Invest ${fmt(product.price_pesewas)} in ${product.name}? Review the product terms before confirming.`)) return;
  try {
    await api('/api/investments', { method: 'POST', body: JSON.stringify({ product_id: productId }) });
    await refreshDashboard();
    notify('Investment recorded. Review its terms in your account.');
  } catch (error) {
    notify(error.message, true);
  }
}

async function refreshAdmin() {
  const data = await api('/api/admin');
  const deposits = data.requests.filter((transaction) => transaction.type === 'deposit');
  const withdrawals = data.requests.filter((transaction) => transaction.type === 'withdrawal');
  $('#depositRequests').innerHTML = deposits.map((transaction) => `<tr><td>${esc(transaction.user_name)}<br><small>+${esc(transaction.user_phone)}</small></td><td>${fmt(transaction.amount_pesewas)}</td><td>${esc(transaction.reference || '—')}</td><td><span class="status ${esc(transaction.status)}">${esc(transaction.status)}</span></td><td>${transaction.status === 'pending' ? `<button class="primary" data-action="review" data-id="${transaction.id}" data-review="approve">Approve</button><button class="danger-btn" data-action="review" data-id="${transaction.id}" data-review="reject">Reject</button>` : '—'}</td></tr>`).join('') || '<tr><td colspan="5">No deposit requests.</td></tr>';
  $('#withdrawRequests').innerHTML = withdrawals.map((transaction) => `<tr><td>${esc(transaction.user_name)}<br><small>+${esc(transaction.user_phone)}</small></td><td>${fmt(transaction.amount_pesewas)}</td><td>${esc(transaction.provider || '—')}</td><td>${esc(transaction.payout_phone || '—')}</td><td><span class="status ${esc(transaction.status)}">${esc(transaction.status)}</span></td><td>${transaction.status === 'pending' ? `<button class="primary" data-action="review" data-id="${transaction.id}" data-review="paid">Mark paid</button><button class="danger-btn" data-action="review" data-id="${transaction.id}" data-review="reject">Reject / refund</button>` : '—'}</td></tr>`).join('') || '<tr><td colspan="6">No withdrawal requests.</td></tr>';
  $('#investorList').innerHTML = data.users.map((user) => `<div class="admin-user"><div><strong>${esc(user.name)} ${user.role === 'admin' ? '<span class="status">ADMIN</span>' : ''} ${user.banned ? '<span class="status banned">DISABLED</span>' : ''}</strong><small>+${esc(user.phone)} · Balance ${fmt(user.balance_pesewas)}</small></div><div class="admin-tools">${user.role !== 'admin' ? `<button class="quiet" data-action="balance" data-id="${user.id}" data-balance="credit">Credit</button><button class="quiet" data-action="balance" data-id="${user.id}" data-balance="debit">Debit</button><button class="quiet" data-action="message" data-id="${user.id}">Message</button><button class="danger-btn" data-action="ban" data-id="${user.id}" data-banned="${!user.banned}">${user.banned ? 'Enable' : 'Disable'}</button>` : '<span class="status">Protected</span>'}</div></div>`).join('') || '<div class="empty">No investor accounts.</div>';
}

async function adminAction(button) {
  const { action, id } = button.dataset;
  if (action === 'review') {
    const review = button.dataset.review;
    const message = review === 'approve' ? 'Confirm the incoming MoMo transfer in the provider records before crediting this account.'
      : review === 'paid' ? 'Only mark this withdrawal paid after you have completed the MoMo payout outside this website.'
        : 'Reject this request? Any reserved withdrawal balance will be returned.';
    if (!confirm(message)) return;
    try {
      await api(`/api/admin/requests/${encodeURIComponent(id)}/review`, { method: 'POST', body: JSON.stringify({ action: review }) });
      await refreshDashboard();
      notify('Request status updated.');
    } catch (error) { notify(error.message, true); }
    return;
  }
  if (action === 'ban') {
    try {
      await api(`/api/admin/users/${encodeURIComponent(id)}/status`, { method: 'POST', body: JSON.stringify({ banned: button.dataset.banned === 'true' }) });
      await refreshAdmin();
      notify('Account status updated.');
    } catch (error) { notify(error.message, true); }
    return;
  }
  const row = button.closest('.admin-user');
  if (action === 'balance') {
    modal(`${button.dataset.balance === 'credit' ? 'Credit' : 'Debit'} investor balance`, `<form><div class="field"><label>Amount (GH₵)</label><input name="amount" type="number" min="0.01" step="0.01" required></div><div class="field"><label>Reason</label><input name="note" maxlength="120" required></div><button class="primary wide">Confirm adjustment</button></form>`, async (formData, root) => {
      try {
        await api(`/api/admin/users/${encodeURIComponent(id)}/balance`, { method: 'POST', body: JSON.stringify({ action: button.dataset.balance, amount: formData.get('amount'), note: formData.get('note') }) });
        root.innerHTML = '';
        await refreshDashboard();
        notify('Balance adjustment recorded in the account ledger.');
      } catch (error) { notify(error.message, true); }
    });
  } else if (action === 'message') {
    modal('Send investor message', `<form><div class="field"><label>Message for ${esc(row?.querySelector('strong')?.textContent || '')}</label><textarea name="message" maxlength="1000" required></textarea></div><button class="primary wide">Send message</button></form>`, async (formData, root) => {
      try {
        await api(`/api/admin/users/${encodeURIComponent(id)}/messages`, { method: 'POST', body: JSON.stringify({ message: formData.get('message') }) });
        root.innerHTML = '';
        await refreshAdmin();
        notify('Message saved to the investor account.');
      } catch (error) { notify(error.message, true); }
    });
  }
}

$('#switchMode').addEventListener('click', () => setMode(!isSignup));
$('#authForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#authError').textContent = '';
  const password = $('#password').value;
  if (isSignup && password !== $('#confirmPassword').value) {
    $('#authError').textContent = 'Passwords do not match.';
    return;
  }
  const phone = $('#phone').value;
  const path = isSignup ? '/api/auth/register' : '/api/auth/login';
  const body = { phone, password };
  if (isSignup) body.name = $('#fullName').value.trim();
  try {
    const result = await api(path, { method: 'POST', body: JSON.stringify(body) });
    currentUser = result.user;
    await refreshDashboard();
  } catch (error) { $('#authError').textContent = error.message; }
});

$('#logoutBtn').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); }
  catch (error) { notify(error.message, true); }
  showAuth();
});

$('#mainNav').addEventListener('click', (event) => {
  const button = event.target.closest('[data-page]');
  if (button) go(button.dataset.page);
});

document.body.addEventListener('click', (event) => {
  const goto = event.target.closest('[data-goto]');
  if (goto) go(goto.dataset.goto);
  const buy = event.target.closest('[data-buy]');
  if (buy) purchase(buy.dataset.buy);
  const adminButton = event.target.closest('[data-action]');
  if (adminButton && currentUser?.role === 'admin') adminAction(adminButton);
});

$('#openDeposit').addEventListener('click', () => modal('Request a deposit', '<p>During maintenance, requests are reviewed manually. Enter the sending MoMo number used as your reference. The review target is within 15 minutes during supported business hours. Funds are credited only after the transfer is verified.</p><form><div class="field"><label>Amount (GH₵)</label><input name="amount" type="number" min="0.01" step="0.01" required></div><div class="field"><label>Sending MoMo number (reference)</label><input name="reference" inputmode="tel" maxlength="40" required placeholder="e.g. 024 000 0000"></div><button class="primary wide">Submit deposit request</button></form>', (data, root) => submitModal(root, '/api/deposits', { amount: data.get('amount'), reference: data.get('reference') }, 'Deposit request submitted for manual review.')));

$('#openWithdraw').addEventListener('click', () => modal('Request a withdrawal', '<p>The requested amount is reserved from your available balance. Payouts are processed manually by an administrator. If rejected, the reserved balance is returned.</p><form><div class="field"><label>Amount (GH₵)</label><input name="amount" type="number" min="0.01" step="0.01" required></div><div class="field"><label>Ghana mobile money provider</label><select name="provider"><option value="mtn">MTN Mobile Money</option><option value="telecel">Telecel Cash</option><option value="at">AT Money</option></select></div><div class="field"><label>Mobile money number</label><input name="phone" inputmode="tel" placeholder="0XX XXX XXXX" required></div><button class="primary wide">Submit withdrawal request</button></form>', (data, root) => submitModal(root, '/api/withdrawals', { amount: data.get('amount'), provider: data.get('provider'), phone: data.get('phone') }, 'Withdrawal request submitted.')));

(async function initialize() {
  setMode(false);
  try {
    const sessionData = await api('/api/session');
    currentUser = sessionData.user;
    if (currentUser) await refreshDashboard();
  } catch (error) {
    $('#authError').textContent = `Service unavailable: ${error.message}`;
  }
}());
