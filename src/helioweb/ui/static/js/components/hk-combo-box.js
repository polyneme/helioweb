// JavaScript
customElements.define('hk-combo-box', class extends HTMLElement {

	/**
	 * The class constructor object
	 */
	constructor () {

		// Always call super first in constructor
		super();

		// Set base properties
		this.endpoint = this.getAttribute('endpoint');
		this.input = this.querySelector('input');
		let delay = this.getAttribute('delay');
		this.delay = delay ? parseFloat(delay) : 500;
		this.debounce = null;

		// Render UI
		this.init();

	}

	/**
	 * Initialize the UI
	 */
	init () {

		// Create datalist
		this.datalist = document.createElement('datalist');
		this.datalist.id = `${this.input.id}-list`;

		// Create hidden field with ID
		this.field = document.createElement('input');
		this.field.type = 'hidden';
		this.field.name = `${this.input.name}_id`;

		// Create hidden note
		let note = document.createElement('div');
		note.className = 'visually-hidden';
		note.textContent = 'Suggested options will display as you type.';
		note.id = `${this.input.id}-describedby`;

		// Associate input with datalist and note
		this.input.setAttribute('list', this.datalist.id);
		this.input.setAttribute('aria-describedby', note.id);

		// Add everything to the UI
		this.append(this.datalist, this.field, note);

		// Listen for input events
		this.input.addEventListener('input', this);
		this.datalist.addEventListener('click', this);

	}

	/**
	 * Handle event listeners
	 * @param  {Event} event The event object
	 */
	handleEvent (event) {
		this[`on${event.type}`](event);
	}

	/**
	 * Handle input updates
	 * @param  {Event} event The event object
	 */
	oninput (event) {
		clearTimeout(this.debounce);
		this.debounce = setTimeout(() => {
			this.updateDatalist();
		}, this.delay);
	}

	/**
	 * Update the datalist
	 */
	async updateDatalist () {

		try {

			// Query the API
			let response = await fetch(this.endpoint, {
				method: 'POST',
				headers: {
					'Content-type': 'application/x-www-form-urlencoded',
					'Hk-Combo-Box': true
				},
				body: new URLSearchParams([[this.input.name, this.input.value]]).toString()
			});

			// If the response is bad, throw error
			if (!response.ok) throw response;

			// Otherwise, get response
			let data = await response.json();

			// Render
			this.renderDatalist(data);

		} catch (error) {
			console.warn(error);
		}

	}

	/**
	 * Render the datalist
	 * @param  {String} data The data to render
	 */
	renderDatalist (data) {

		// Render datalist content
		this.datalist.innerHTML =
			data.map(function (item) {
				return `<option>${item.value}</option>`;
			}).join('');

		// If selected text matches valid option, set ID field
		let selected = data.find((item) => item.value === this.input.value);
		if (selected) {
			this.field.value = selected.id;
		}

		// Set [pattern] attribute for validation
		let pattern = data.map((item) => item.value.replace('(', '\\(').replace(')','\\)')).join('|');
		this.input.setAttribute('pattern', pattern);

	}

});