/*========================== State ============================*/
let profiles = {};
let fonts = [];
let previewDebounce = null;

const DEFAULT_CELL = () => ({
    type: 'text',
    content: '',
    font: 'LiberationSans-Bold',
    font_size: 60,
    stretch_x: 1.0,
    stretch_y: 1.0,
    invert: false,
    rotation: 0,
    align_h: 'center',
    align_v: 'center',
    padding_top: 2,
    padding_bottom: 2,
    padding_left: 4,
    padding_right: 4,
    weight: 1.0,
    qr_error_correction: 'M',
    barcode_format: 'code128',
    image_data: null,
});

const DEFAULT_ROW = () => ({
    weight: 1.0,
    cells: [DEFAULT_CELL()],
    _collapsed: false,
});

let rows = [DEFAULT_ROW()];

/*========================== Init ============================*/
async function init() {
    await Promise.all([loadProfiles(), loadFonts(), loadStatus()]);
    renderGrid();
    updatePreview();
    setInterval(loadStatus, 10000);
}

async function loadProfiles() {
    const res = await fetch('/api/profiles');
    profiles = await res.json();
    const sel = document.getElementById('tapeProfile');
    sel.innerHTML = '';
    for (const [key, p] of Object.entries(profiles)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = p.name;
        if (key === '12mm_TZe') opt.selected = true;
        sel.appendChild(opt);
    }
    // Set printer URL
    const urlRes = await fetch('/api/printer-url');
    const urlData = await urlRes.json();
    document.getElementById('printerLink').href = urlData.url;
}

async function loadFonts() {
    const res = await fetch('/api/fonts');
    fonts = await res.json();
}

async function loadStatus() {
    try {
        const res = await fetch('/api/status');
        const s = await res.json();
        const dot = document.getElementById('statusDot');
        const txt = document.getElementById('statusText');
        if (!s.reachable) {
            dot.className = 'status-dot err';
            txt.textContent = 'OFFLINE';
        } else if (s.tape_end_error || s.cover_open) {
            dot.className = 'status-dot err';
            txt.textContent = 'ERROR';
        } else if (s.printing) {
            dot.className = 'status-dot warn';
            txt.textContent = 'PRINTING';
        } else {
            dot.className = 'status-dot ok';
            txt.textContent = s.status || 'READY';
        }
        window._lastStatus = s;
    } catch (e) {
        document.getElementById('statusDot').className = 'status-dot';
        document.getElementById('statusText').textContent = '?';
    }
}

/*========================== Grid ============================*/
function renderGrid() {
    const el = document.getElementById('gridEditor');
    el.innerHTML = '';
    rows.forEach((row, ri) => {
        el.appendChild(buildRowCard(row, ri));
    });
}

function buildRowCard(row, ri) {
    const card = document.createElement('div');
    card.className = 'row-card' + (row._collapsed ? ' collapsed' : '');
    card.innerHTML = `
    <div class="row-header" onclick="toggleRow(${ri})">
      <div class="row-header-left">
        <span class="row-label">ROW ${ri + 1}</span>
        <span style="font-size:11px;color:var(--muted)">weight: 
          <input type="number" value="${row.weight}" min="0.1" step="0.1" max="20"
            style="width:50px;padding:2px 4px;font-size:11px;display:inline"
            onclick="event.stopPropagation()"
            onchange="rows[${ri}].weight=parseFloat(this.value)||1;onStateChange()">
        </span>
      </div>
      <div class="row-header-right">
        <span style="font-family:var(--mono);font-size:10px;color:var(--muted)">
          ${row.cells.length} cell${row.cells.length !== 1 ? 's' : ''}
        </span>
        ${rows.length > 1 ? `<button class="btn btn-danger" onclick="event.stopPropagation();removeRow(${ri})">✕</button>` : ''}
        <span style="color:var(--muted);font-size:12px">${row._collapsed ? '▼' : '▲'}</span>
      </div>
    </div>
    <div class="row-body collapsible">
      <div id="cells-${ri}"></div>
      <button class="btn btn-secondary btn-sm" onclick="addCell(${ri})">+ Cell</button>
    </div>
  `;
    const cellsEl = card.querySelector(`#cells-${ri}`);
    row.cells.forEach((cell, ci) => {
        cellsEl.appendChild(buildCellCard(cell, ri, ci));
    });
    return card;
}

function buildCellCard(cell, ri, ci) {
    const card = document.createElement('div');
    card.className = 'cell-card';
    card.innerHTML = `
    <div class="cell-header">
      <span class="cell-type-badge">${cell.type.toUpperCase()}</span>
      <div style="display:flex;align-items:center;gap:6px">
        ${row_cell_count(ri) > 1 ? `<button class="btn btn-danger" onclick="removeCell(${ri},${ci})">✕</button>` : ''}
      </div>
    </div>

    <!-- Type + content -->
    <div class="field">
      <label>Type</label>
      <select onchange="setCellProp(${ri},${ci},'type',this.value);renderGrid()">
        ${['text', 'qr', 'barcode', 'image'].map(t =>
        `<option value="${t}" ${cell.type === t ? 'selected' : ''}>${t.toUpperCase()}</option>`
    ).join('')}
      </select>
    </div>

    ${cell.type === 'text' ? buildTextFields(cell, ri, ci) : ''}
    ${cell.type === 'qr' ? buildQrFields(cell, ri, ci) : ''}
    ${cell.type === 'barcode' ? buildBarcodeFields(cell, ri, ci) : ''}
    ${cell.type === 'image' ? buildImageFields(cell, ri, ci) : ''}

    <!-- Layout -->
    <details style="margin-top:6px">
      <summary style="font-family:var(--mono);font-size:10px;color:var(--muted);cursor:pointer;user-select:none">
        LAYOUT &amp; PADDING
      </summary>
      <div style="margin-top:8px">
        ${buildLayoutFields(cell, ri, ci)}
      </div>
    </details>
  `;
    return card;
}

function buildTextFields(cell, ri, ci) {
    const fontOpts = fonts.map(f =>
        `<option value="${f.name}" ${cell.font === f.name ? 'selected' : ''}>${f.name}</option>`
    ).join('');
    return `
    <div class="field">
      <label>Content (use \\n for new lines)</label>
      <textarea rows="2" style="resize:vertical;font-family:var(--mono);font-size:12px"
        onchange="setCellProp(${ri},${ci},'content',this.value)"
        oninput="setCellProp(${ri},${ci},'content',this.value)"
      >${escHtml(cell.content)}</textarea>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Font</label>
        <select onchange="setCellProp(${ri},${ci},'font',this.value)">
          ${fontOpts}
        </select>
      </div>
      <div class="field">
        <label>Font Size</label>
        <input type="number" value="${cell.font_size}" min="6" max="600"
          onchange="setCellProp(${ri},${ci},'font_size',parseInt(this.value))">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Stretch X</label>
        <input type="number" value="${cell.stretch_x}" min="0.1" max="8" step="0.1"
          onchange="setCellProp(${ri},${ci},'stretch_x',parseFloat(this.value))">
      </div>
      <div class="field">
        <label>Stretch Y</label>
        <input type="number" value="${cell.stretch_y}" min="0.1" max="8" step="0.1"
          onchange="setCellProp(${ri},${ci},'stretch_y',parseFloat(this.value))">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Rotation</label>
        <select onchange="setCellProp(${ri},${ci},'rotation',parseInt(this.value))">
          ${[0, 90, 180, 270].map(r => `<option value="${r}" ${cell.rotation === r ? 'selected' : ''}>${r}°</option>`).join('')}
        </select>
      </div>
      <div class="field">
        <label style="display:flex;align-items:center;gap:6px">
          <input type="checkbox" ${cell.invert ? 'checked' : ''} 
            onchange="setCellProp(${ri},${ci},'invert',this.checked)">
          Invert
        </label>
      </div>
    </div>
  `;
}

function buildQrFields(cell, ri, ci) {
    return `
    <div class="field">
      <label>QR Content / URL</label>
      <input type="text" value="${escHtml(cell.content)}"
        onchange="setCellProp(${ri},${ci},'content',this.value)"
        oninput="setCellProp(${ri},${ci},'content',this.value)"
        placeholder="https://example.com">
    </div>
    <div class="field">
      <label>Error Correction</label>
      <select onchange="setCellProp(${ri},${ci},'qr_error_correction',this.value)">
        ${['L', 'M', 'Q', 'H'].map(e => `<option value="${e}" ${cell.qr_error_correction === e ? 'selected' : ''}>${e}</option>`).join('')}
      </select>
    </div>
  `;
}

function buildBarcodeFields(cell, ri, ci) {
    return `
    <div class="field">
      <label>Barcode Content</label>
      <input type="text" value="${escHtml(cell.content)}"
        onchange="setCellProp(${ri},${ci},'content',this.value)"
        oninput="setCellProp(${ri},${ci},'content',this.value)">
    </div>
    <div class="field">
      <label>Format</label>
      <select onchange="setCellProp(${ri},${ci},'barcode_format',this.value)">
        ${['code128', 'code39', 'ean13', 'ean8', 'upca'].map(f =>
        `<option value="${f}" ${cell.barcode_format === f ? 'selected' : ''}>${f.toUpperCase()}</option>`
    ).join('')}
      </select>
    </div>
  `;
}

function buildImageFields(cell, ri, ci) {
    return `
    <div class="field">
      <label>Image Upload</label>
      <input type="file" accept="image/*" style="font-size:12px"
        onchange="uploadImage(event,${ri},${ci})">
      ${cell.image_data ? '<div style="color:var(--green);font-size:11px;margin-top:4px">✓ Image loaded</div>' : ''}
    </div>
  `;
}

function buildLayoutFields(cell, ri, ci) {
    const h_icons = { 'left': '⫷', 'center': '≡', 'right': '⫸' };
    const v_icons = { 'top': '⤒', 'center': '↕', 'bottom': '⤓' };
    return `
    <div class="field-row">
      <div class="field">
        <label>Align H</label>
        <div class="align-btn-row">
          ${Object.entries(h_icons).map(([v, icon]) => `
            <div class="align-btn ${cell.align_h === v ? 'active' : ''}"
              onclick="setCellProp(${ri},${ci},'align_h','${v}');renderGrid()">${icon}</div>
          `).join('')}
        </div>
      </div>
      <div class="field">
        <label>Align V</label>
        <div class="align-btn-row">
          ${Object.entries(v_icons).map(([v, icon]) => `
            <div class="align-btn ${cell.align_v === v ? 'active' : ''}"
              onclick="setCellProp(${ri},${ci},'align_v','${v}');renderGrid()">${icon}</div>
          `).join('')}
        </div>
      </div>
    </div>
    <div class="field">
      <label>Cell Width Weight</label>
      <input type="number" value="${cell.weight}" min="0.1" max="20" step="0.1"
        onchange="setCellProp(${ri},${ci},'weight',parseFloat(this.value))">
    </div>
    <div class="field">
      <label>Padding (T / B / L / R)</label>
      <div class="field-row" style="grid-template-columns:1fr 1fr 1fr 1fr">
        ${['padding_top', 'padding_bottom', 'padding_left', 'padding_right'].map((p, i) => `
          <input type="number" value="${cell[p]}" min="0" max="200" placeholder="${['T', 'B', 'L', 'R'][i]}"
            title="${p.replace('_', ' ')}"
            onchange="setCellProp(${ri},${ci},'${p}',parseInt(this.value)||0)">
        `).join('')}
      </div>
    </div>
  `;
}

/*========================== Grid Mutations ============================*/
function row_cell_count(ri) { return rows[ri]?.cells?.length || 0; }

function toggleRow(ri) {
    rows[ri]._collapsed = !rows[ri]._collapsed;
    renderGrid();
}

function addRow() {
    rows.push(DEFAULT_ROW());
    renderGrid();
    onStateChange();
}

function removeRow(ri) {
    rows.splice(ri, 1);
    renderGrid();
    onStateChange();
}

function addCell(ri) {
    rows[ri].cells.push(DEFAULT_CELL());
    renderGrid();
    onStateChange();
}

function removeCell(ri, ci) {
    rows[ri].cells.splice(ci, 1);
    renderGrid();
    onStateChange();
}

function setCellProp(ri, ci, key, value) {
    rows[ri].cells[ci][key] = value;
    onStateChange();
}

/*========================== Build Spec From UI ============================*/
function buildSpec() {
    return {
        tape_profile: document.getElementById('tapeProfile').value,
        label_rotation: parseInt(document.getElementById('labelRotation').value),
        min_label_width: parseInt(document.getElementById('minLabelWidth').value) || 0,
        top_margin: parseInt(document.getElementById('topMargin').value) || 8,
        bottom_margin: parseInt(document.getElementById('bottomMargin').value) || 8,
        copies: parseInt(document.getElementById('copies').value) || 1,
        cut_mode: document.getElementById('cutMode').value,
        rows: rows.map(r => ({
            weight: r.weight,
            cells: r.cells.map(c => ({ ...c })),
        })),
    };
}

/*========================== Preview ============================*/
function onStateChange() {
    clearTimeout(previewDebounce);
    previewDebounce = setTimeout(updatePreview, 350);
}

async function updatePreview() {
    const spinner = document.getElementById('previewSpinner');
    spinner.style.display = 'block';
    try {
        const spec = buildSpec();
        const res = await fetch('/api/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(spec),
        });
        if (!res.ok) {
            const err = await res.json();
            showToast('Preview error: ' + err.detail, 'err');
            return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const img = document.getElementById('previewImg');
        img.onload = () => {
            document.getElementById('previewDims').textContent =
                `${img.naturalWidth} × ${img.naturalHeight} px`;
            URL.revokeObjectURL(url);
        };
        img.src = url;
    } catch (e) {
        showToast('Preview failed: ' + e.message, 'err');
    } finally {
        spinner.style.display = 'none';
    }
}

/*========================== Printing ============================*/
async function doPrint() {
    const spec = buildSpec();
    try {
        const res = await fetch('/api/print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(spec),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'ok');
            loadStatus();
        } else {
            showToast('Print error: ' + data.detail, 'err');
        }
    } catch (e) {
        showToast('Print failed: ' + e.message, 'err');
    }
}

/*========================== Batch Printing ============================*/
function parseBatchInput() {
    const raw = document.getElementById('batchInput').value.trim();
    try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map(String);
    } catch (_) { }
    return raw.split('\n').map(l => l.trim()).filter(Boolean);
}

async function batchPreview() {
    const items = parseBatchInput();
    if (!items.length) { showToast('No items', 'err'); return; }
    const spec = buildSpec();
    // Preview first item
    const testSpec = JSON.parse(JSON.stringify(spec));
    injectFirstText(testSpec, items[0]);
    const res = await fetch('/api/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(testSpec),
    });
    if (res.ok) {
        const blob = await res.blob();
        const img = document.getElementById('previewImg');
        img.src = URL.createObjectURL(blob);
        switchTab('preview', document.querySelector('.tab'));
    }
}

async function batchPrint() {
    const items = parseBatchInput();
    if (!items.length) { showToast('No items', 'err'); return; }
    const statusEl = document.getElementById('batchStatus');
    statusEl.textContent = 'Sending…';
    const res = await fetch('/api/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template: buildSpec(), items }),
    });
    const data = await res.json();
    statusEl.textContent = `Sent ${data.sent}/${items.length}`;
    if (data.errors.length) showToast(`${data.errors.length} errors`, 'err');
    else showToast(`Printed ${data.sent} label(s)`, 'ok');
}

function injectFirstText(spec, text) {
    for (const row of spec.rows) {
        for (const cell of row.cells) {
            if (cell.type === 'text') { cell.content = text; return; }
        }
    }
}

/*========================== Image Upload ============================*/
async function uploadImage(event, ri, ci) {
    const file = event.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    setCellProp(ri, ci, 'image_data', data.image_data);
    renderGrid();
    onStateChange();
}

/*========================== Status Modal ============================*/
async function openStatusModal() {
    document.getElementById('statusModal').classList.add('open');
    await loadStatus();
    const s = window._lastStatus || {};
    const body = document.getElementById('statusModalBody');
    const rows_data = [
        ['Status', s.status || '—', s.status === 'READY' ? 'ok' : 'err'],
        ['Reachable', s.reachable ? 'Yes' : 'No', s.reachable ? 'ok' : 'err'],
        ['Firmware', s.firmware || '—', ''],
        ['Serial', s.serial || '—', ''],
        ['Media', s.media_type || '—', ''],
        ['Page Count', s.page_count || '—', ''],
        ['Tape End Error', s.tape_end_error ? 'Yes' : 'No', s.tape_end_error ? 'err' : 'ok'],
        ['Cover Open', s.cover_open ? 'Yes' : 'No', s.cover_open ? 'err' : 'ok'],
        ['Busy', s.busy ? 'Yes' : 'No', ''],
        ['Printing', s.printing ? 'Yes' : 'No', ''],
        ['Waiting for Data', s.waiting_for_data ? 'Yes' : 'No', ''],
    ];
    body.innerHTML = rows_data.map(([k, v, cls]) =>
        `<div class="status-row">
       <span class="status-key">${k}</span>
       <span class="status-val ${cls}">${v}</span>
     </div>`
    ).join('');
}

function closeStatusModal() {
    document.getElementById('statusModal').classList.remove('open');
}

/*========================== Tabs ============================*/
function switchTab(name, el) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab')[name === 'preview' ? 0 : 1].classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
}

/*========================== Toast ============================*/
let toastTimer;
function showToast(msg, type = '') {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.className = 'toast show ' + type;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
}

/*========================== Helpers ============================*/
function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/*========================== Boot ============================*/
init();