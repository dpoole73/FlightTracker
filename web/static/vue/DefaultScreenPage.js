/**
 * Default Screen page - idle screen theme rotation and per-theme settings.
 */

import { defineComponent } from "./vendor.js";

// Known idle themes. Order here only affects the "Add a screen" button
// order, not the rotation order (that's store.config.idle_theme_order).
const THEME_META = {
  classic: { label: "Classic", icon: "bi-clock" },
  forecast: { label: "Forecast", icon: "bi-cloud-sun-fill" },
  conditions: { label: "Current Conditions", icon: "bi-thermometer-half" },
  stock: { label: "Stock Ticker", icon: "bi-graph-up-arrow" },
  solar: { label: "Solar Panel", icon: "bi-sun" },
  solar_history: { label: "Solar History", icon: "bi-bar-chart-fill" },
  solar_intraday: { label: "Solar Today", icon: "bi-bar-chart-line" },
};

export default defineComponent({
  name: "DefaultScreenPage",
  props: {
    store: { type: Object, required: true },
  },
  data() {
    return { themeMeta: THEME_META };
  },
  computed: {
    activeThemes() {
      return this.store.config.idle_theme_order || [];
    },
    availableThemes() {
      const active = this.activeThemes;
      return Object.keys(THEME_META).filter((k) => !active.includes(k));
    },
    forecastDuration: {
      get() {
        return this.store.config.theme?.forecast?.duration || "3hour";
      },
      set(val) {
        if (!this.store.config.theme) this.store.config.theme = {};
        if (!this.store.config.theme.forecast) this.store.config.theme.forecast = {};
        this.store.config.theme.forecast.duration = val;
      },
    },
    conditionsDisableScroll: {
      get() {
        return this.store.config.theme?.conditions?.disable_description_scroll || false;
      },
      set(val) {
        if (!this.store.config.theme) this.store.config.theme = {};
        if (!this.store.config.theme.conditions) this.store.config.theme.conditions = {};
        this.store.config.theme.conditions.disable_description_scroll = val;
      },
    },
  },
  methods: {
    addTheme(key) {
      this.store.config.idle_theme_order = [...this.activeThemes, key];
    },
    removeTheme(key) {
      this.store.config.idle_theme_order = this.activeThemes.filter((k) => k !== key);
    },
    moveUp(index) {
      if (index === 0) return;
      const list = [...this.activeThemes];
      [list[index - 1], list[index]] = [list[index], list[index - 1]];
      this.store.config.idle_theme_order = list;
    },
    moveDown(index) {
      const list = [...this.activeThemes];
      if (index === list.length - 1) return;
      [list[index], list[index + 1]] = [list[index + 1], list[index]];
      this.store.config.idle_theme_order = list;
    },
  },
  template: `
    <div>
    <h2 class="fs-4 fw-semibold mb-3"><i class="bi bi-house me-2"></i>Default Screen</h2>

    <!-- ====== Idle screen rotation ====== -->
    <div id="group-theme" class="card mb-3 p-3">
      <p class="section-heading"><i class="bi bi-palette me-2"></i>Idle Screens</p>
      <p class="form-text text-muted small mb-2">
        Choose what to display when no flights or satellites are overhead. Enable more than
        one to rotate between them.
      </p>

      <!-- Serialised for the classic form POST, same approach as satellite_norad_ids -->
      <input type="hidden" name="idle_theme_order" :value="activeThemes.join(',')" />

      <ul class="list-group mb-3">
        <li v-for="(key, index) in activeThemes" :key="key"
            class="list-group-item d-flex align-items-center justify-content-between">
          <span><i :class="'bi me-2 ' + themeMeta[key].icon"></i>{{ themeMeta[key].label }}</span>
          <span>
            <button type="button" class="btn btn-sm btn-outline-secondary me-1"
                    :disabled="index === 0" @click="moveUp(index)" title="Move up">
              <i class="bi bi-arrow-up"></i>
            </button>
            <button type="button" class="btn btn-sm btn-outline-secondary me-1"
                    :disabled="index === activeThemes.length - 1" @click="moveDown(index)" title="Move down">
              <i class="bi bi-arrow-down"></i>
            </button>
            <button type="button" class="btn btn-sm btn-outline-danger"
                    @click="removeTheme(key)" title="Disable">
              <i class="bi bi-x-lg"></i>
            </button>
          </span>
        </li>
        <li v-if="activeThemes.length === 0" class="list-group-item text-muted small">
          No idle screens enabled - add one below.
        </li>
      </ul>

      <div v-if="availableThemes.length" class="mb-2">
        <span class="text-muted small me-2">Add a screen:</span>
        <button v-for="key in availableThemes" :key="key" type="button"
                class="btn btn-sm btn-outline-primary me-1 mb-1"
                @click="addTheme(key)">
          <i :class="'bi me-1 ' + themeMeta[key].icon"></i>{{ themeMeta[key].label }}
        </button>
      </div>

      <div v-show="activeThemes.length > 1" class="mt-3">
        <h5>Rotation Interval</h5>
        <div class="input-group input-group-sm" style="max-width:180px">
          <input type="number" class="form-control" name="idle_theme_rotation_seconds"
                 v-model.number="store.config.idle_theme_rotation_seconds" min="3" max="600" />
          <span class="input-group-text">seconds</span>
        </div>
        <div class="form-text text-muted small">How long each idle screen stays up before rotating to the next.</div>
      </div>
    </div>

    <!-- ====== Classic theme ====== -->
    <div v-show="activeThemes.includes('classic')">
      <div id="group-weather" class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-cloud-sun me-2"></i>Weather</p>

        <div class="mb-3">
          <h5>Weather Display</h5>
          <div class="form-check">
            <input type="radio" class="form-check-input" name="weather_mode" id="weather_off"
                   :value="0" v-model.number="store.config.weather_mode" />
            <label class="form-check-label" for="weather_off">Off</label>
          </div>
          <div class="form-check">
            <input type="radio" class="form-check-input" name="weather_mode" id="weather_temp"
                   :value="1" v-model.number="store.config.weather_mode" />
            <label class="form-check-label" for="weather_temp">Temperature</label>
          </div>
          <div class="form-check mb-1">
            <input type="radio" class="form-check-input" name="weather_mode" id="weather_rain"
                   :value="2" v-model.number="store.config.weather_mode" />
            <label class="form-check-label" for="weather_rain">Temperature + rainfall graph</label>
          </div>
          <p class="text-muted small mb-1">
            The rainfall graph shows the forecast for the next 24 hours, hour by hour.
          </p>

          <div v-show="store.config.weather_mode === 2">
            <h5 class="mt-2">Rainfall Sensitivity</h5>
            <select class="form-select form-select-sm" name="rain_sensitivity" style="max-width:260px"
                    v-model.number="store.config.rain_sensitivity">
              <option :value="0">Not very rainy - desert / arid (1 mm)</option>
              <option :value="1">Moderately rainy - UK / Europe (3 mm)</option>
              <option :value="2">Very rainy - tropics / monsoon (9 mm)</option>
            </select>
            <div class="form-text text-muted small">
              Sets the full-scale value of the graph. Increase if your bars are always full; decrease if they're always empty.
            </div>
          </div>
        </div>

        <div class="card bg-light p-3 mb-3">
          <h5 class="pb-3">How the rainfall graph works</h5>
          <div class="row g-3 align-items-center pb-3">
            <div class="col-12 col-sm-5 text-center">
              <img :src="store.ui.staticUrls.weatherExplained" class="img-fluid mx-auto d-block"
                   style="max-height:150px;" alt="Weather graph example" />
            </div>
            <div class="col-12 col-sm-7">
              <p class="small mb-1">Each column is one hour, the leftmost is <strong>now</strong>.</p>
              <p class="small mb-0">The height of the column shows how much rain is expected that hour.</p>
            </div>
          </div>
          <hr class="pb-3">
          <div class="row g-3 align-items-center">
            <div class="col-12 col-sm-5 text-center">
              <img :src="store.ui.staticUrls.scaleExplained" class="img-fluid mx-auto d-block"
                   style="max-height:75px" alt="Temperature colour scale" />
            </div>
            <div class="col-12 col-sm-7">
              <p class="small mb-0">The colour of each column shows the temperature at that hour.</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ====== Forecast theme ====== -->
    <div v-show="activeThemes.includes('forecast')">
      <div class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-cloud-sun-fill me-2"></i>Forecast</p>
        <p class="text-muted small mb-0">Display upcoming weather forecasts as icons on the idle screen.</p>
      </div>
      <div class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-clock-history me-2"></i>Forecast Duration</p>
        <div class="mb-3">
          <h5>Forecast Type</h5>
          <div class="form-check">
            <input type="radio" class="form-check-input" name="theme_forecast_duration"
                   id="forecast_duration_3hour" value="3hour" v-model="forecastDuration" />
            <label class="form-check-label" for="forecast_duration_3hour">3 hour (now + next 2 hours)</label>
          </div>
          <div class="form-check">
            <input type="radio" class="form-check-input" name="theme_forecast_duration"
                   id="forecast_duration_12hour" value="12hour" v-model="forecastDuration" />
            <label class="form-check-label" for="forecast_duration_12hour">12 hour (now + 4h + 8h)</label>
          </div>
          <div class="form-check">
            <input type="radio" class="form-check-input" name="theme_forecast_duration"
                   id="forecast_duration_3day" value="3day" v-model="forecastDuration" />
            <label class="form-check-label" for="forecast_duration_3day">3 day (today + next 2 days)</label>
          </div>
          <div class="form-text text-muted small">Choose how far ahead the forecast display looks.</div>
        </div>
      </div>
    </div>

    <!-- ====== Current Conditions theme ====== -->
    <div v-show="activeThemes.includes('conditions')">
      <div class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-thermometer-half me-2"></i>Current Conditions</p>
        <p class="text-muted small mb-0">
          Display the current weather at a glance: a weather sprite, temperature, humidity, wind, UV index,
          moon-phase and sunrise/sunset.
        </p>
      </div>
      <div class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-card-text me-2"></i>Description</p>
        <div class="mb-2 form-check">
          <input type="checkbox" class="form-check-input" name="theme_conditions_disable_scroll"
                 id="theme_conditions_disable_scroll" v-model="conditionsDisableScroll" />
          <label class="form-check-label" for="theme_conditions_disable_scroll">Disable scrolling weather description</label>
        </div>
      </div>
    </div>

    <!-- ====== Stock theme ====== -->
    <div v-show="activeThemes.includes('stock')">
      <div class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-graph-up-arrow me-2"></i>Stock Ticker</p>
        <p class="text-muted small mb-2">
          Shows a live stock price and change percentage, via the
          <a href="https://www.alphavantage.co/support/#api-key" target="_blank" rel="noopener noreferrer">Alpha Vantage</a>
          free API.
        </p>

        <div class="mb-3">
          <label class="form-label small" for="stock_symbol">Symbol</label>
          <input type="text" maxlength="8" class="form-control form-control-sm" style="max-width:120px;text-transform:uppercase"
                 name="stock_symbol" id="stock_symbol"
                 :value="store.config.stock_symbol"
                 @input="store.config.stock_symbol = $event.target.value.toUpperCase()"
                 placeholder="e.g. MSFT" />
        </div>

        <div class="mb-3">
          <label class="form-label small" for="stock_api_key">API Key</label>
          <input type="text" class="form-control form-control-sm" style="max-width:320px"
                 name="stock_api_key" id="stock_api_key"
                 v-model="store.config.stock_api_key" placeholder="Alpha Vantage API key" />
        </div>

        <div class="mb-1">
          <label class="form-label small" for="stock_refresh_seconds">Refresh interval (seconds)</label>
          <input type="number" class="form-control form-control-sm" style="max-width:140px"
                 name="stock_refresh_seconds" id="stock_refresh_seconds"
                 v-model.number="store.config.stock_refresh_seconds" min="60" max="3600" />
          <div class="form-text text-muted small">
            The free API tier is rate-limited - don't set this too low.
          </div>
        </div>
      </div>
    </div>

    <!-- ====== Solar Live (local) + Solar History (cloud, needs account) ====== -->
    <div v-show="activeThemes.includes('solar') || activeThemes.includes('solar_intraday') || activeThemes.includes('solar_history')">
      <div v-show="activeThemes.includes('solar_history')" class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-sun me-2"></i>Solar - Enphase Account</p>
        <p class="text-muted small mb-2">
          Used by the History screen below (daily totals aren't available locally). Requires
          a one-time OAuth setup outside this app (see the project README) to obtain the
          initial refresh token.
        </p>

        <div class="row g-2 mb-2">
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_client_id">Client ID</label>
            <input type="text" class="form-control form-control-sm"
                   name="solar_client_id" id="solar_client_id"
                   v-model="store.config.solar_client_id"
                   placeholder="Enphase app client ID" autocomplete="off" />
          </div>
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_client_secret">Client Secret</label>
            <input type="password" class="form-control form-control-sm"
                   name="solar_client_secret" id="solar_client_secret"
                   v-model="store.config.solar_client_secret"
                   placeholder="Enphase app client secret" autocomplete="new-password" />
          </div>
        </div>

        <div class="row g-2 mb-2">
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_api_key">API Key</label>
            <input type="password" class="form-control form-control-sm"
                   name="solar_api_key" id="solar_api_key"
                   v-model="store.config.solar_api_key"
                   placeholder="Enphase API key" autocomplete="new-password" />
          </div>
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_system_id">System ID</label>
            <input type="text" class="form-control form-control-sm"
                   name="solar_system_id" id="solar_system_id"
                   v-model="store.config.solar_system_id"
                   placeholder="e.g. 2631222" autocomplete="off" />
          </div>
        </div>

        <div class="mb-1">
          <label class="form-label small" for="solar_refresh_token">Initial Refresh Token</label>
          <input type="password" class="form-control form-control-sm"
                 name="solar_refresh_token" id="solar_refresh_token"
                 v-model="store.config.solar_refresh_token"
                 placeholder="One-time seed from the OAuth authorization-code exchange"
                 autocomplete="new-password" />
          <div class="form-text text-muted small">
            Only used to bootstrap the first token refresh. After that the app manages its
            own rotating token automatically - you shouldn't need to touch this again unless
            you re-authorize from scratch.
          </div>
        </div>
      </div>

      <!-- Solar Live -->
      <div v-show="activeThemes.includes('solar') || activeThemes.includes('solar_intraday')" class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-lightning-charge-fill me-2"></i>Solar - Live Power</p>
        <p class="text-muted small mb-2">
          Shared by the Live Power and Solar Today screens below - both read directly from
          your Envoy on the local network (not the Enphase cloud, so no rate limit). Uses its
          own token here, separate from the Enphase account details above.
        </p>

        <div class="row g-2 mb-2">
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_local_host">Envoy Address</label>
            <input type="text" class="form-control form-control-sm"
                   name="solar_local_host" id="solar_local_host"
                   v-model="store.config.solar_local_host"
                   placeholder="e.g. 192.168.4.72" autocomplete="off" />
          </div>
          <div class="col-12 col-sm-6">
            <label class="form-label small" for="solar_local_token">Local Access Token</label>
            <input type="password" class="form-control form-control-sm"
                   name="solar_local_token" id="solar_local_token"
                   v-model="store.config.solar_local_token"
                   placeholder="Long-lived local token" autocomplete="new-password" />
          </div>
        </div>

        <div class="mb-1">
          <label class="form-label small" for="solar_refresh_seconds">Refresh interval (seconds)</label>
          <input type="number" class="form-control form-control-sm" style="max-width:140px"
                 name="solar_refresh_seconds" id="solar_refresh_seconds"
                 v-model.number="store.config.solar_refresh_seconds" min="2" max="86400" />
          <div class="form-text text-muted small">
            No rate limit on the local network - a few seconds is fine.
          </div>
        </div>
      </div>

      <!-- Solar History -->
      <div v-show="activeThemes.includes('solar_history')" class="card mb-3 p-3">
        <p class="section-heading"><i class="bi bi-bar-chart-fill me-2"></i>Solar - History</p>
        <p class="text-muted small mb-2">
          Shows a bar graph of daily usage vs. generation over the lookback window below.
        </p>
        <div class="row g-2">
          <div class="col-auto">
            <label class="form-label small" for="solar_lookback_days">Lookback window (days)</label>
            <input type="number" class="form-control form-control-sm" style="width:8rem"
                   name="solar_lookback_days" id="solar_lookback_days"
                   v-model.number="store.config.solar_lookback_days" min="1" max="16" />
            <div class="form-text text-muted small">Max 16 - limited by how many bars fit on the panel.</div>
          </div>
          <div class="col-auto">
            <label class="form-label small" for="solar_history_refresh_seconds">Refresh interval (seconds)</label>
            <input type="number" class="form-control form-control-sm" style="width:9rem"
                   name="solar_history_refresh_seconds" id="solar_history_refresh_seconds"
                   v-model.number="store.config.solar_history_refresh_seconds" min="300" max="86400" />
            <div class="form-text text-muted small">Daily totals barely change intraday - hourly is plenty.</div>
          </div>
        </div>
      </div>
    </div>
    </div>
  `,
});
