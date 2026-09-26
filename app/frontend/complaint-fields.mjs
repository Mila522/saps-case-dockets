import categories from './crime-categories.json' with {type:'json'};

export const CRIME_CATEGORIES = Object.freeze(categories);
export const categoryChoices = [['', 'Select a crime category'], ...categories.map(value=>[value,value])];

export function complaintPayload(values) {
  const {crime_category_other, ...payload} = values;
  const category = (payload.crime_category || '').trim();
  if (!categories.includes(category)) throw new Error('Select a crime category.');
  if (category === 'Other') {
    const detail = (crime_category_other || '').trim();
    if (!detail || [...detail].length > 143) throw new Error('Please specify the crime category using 1–143 characters.');
    payload.crime_category = 'Other: ' + detail;
  } else payload.crime_category = category;
  return payload;
}

export function bindCrimeCategory(form) {
  const select = form.querySelector('[name=crime_category]');
  const group = document.createElement('div'); group.className = 'field other-category';
  const label = document.createElement('label');
  const other = document.createElement('input');
  other.name = 'crime_category_other'; other.id = select.id + '-other';
  // Validate Unicode characters consistently with the server, not UTF-16 units.
  other.maxLength = 286;
  label.htmlFor = other.id; label.textContent = 'Please specify the crime category';
  const hint = document.createElement('small'); hint.textContent = 'Maximum 143 characters.';
  hint.id = other.id + '-hint'; other.setAttribute('aria-describedby', hint.id);
  group.append(label, other, hint); select.after(group);
  const validate = () => other.setCustomValidity(other.required && (!other.value.trim() || [...other.value.trim()].length > 143)
    ? 'Please specify the crime category using 1–143 characters.' : '');
  const sync = () => {
    const visible = select.value === 'Other';
    group.hidden = !visible; other.disabled = !visible; other.required = visible;
    if (!visible) other.value = '';
    validate();
  };
  select.addEventListener('change', sync); other.addEventListener('input', validate);
  form.addEventListener('reset', () => setTimeout(sync, 0)); sync();
}

export const stationLabel = station => `${station.name} — ${[station.city,station.province].filter(Boolean).join(', ')}`;
export function stationAddress(station) {
  return station ? [station.address_line_1, station.address_line_2, station.city, station.province].filter(Boolean).join(', ') : '';
}
