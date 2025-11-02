# Testing Checklist

## Phase 1: Local BLE Connection

- [ ] Integration appears in HA integrations list
- [ ] Discovery notification appears when battery in range
- [ ] Device can be added via config flow
- [ ] All sensor entities created
- [ ] Battery voltage/current/SOC values update
- [ ] Cell voltages populate correctly
- [ ] Power sensors calculate correctly
- [ ] Text sensors show device info
- [ ] Binary sensors show connection status
- [ ] Buttons trigger commands (check logs)
- [ ] Switches toggle successfully
- [ ] Selects change modes successfully
- [ ] Entities unavailable when battery out of range
- [ ] Entities restore when battery back in range

## Phase 2: Multiple Batteries

- [ ] Second battery discovered independently
- [ ] Second battery creates separate config entry
- [ ] Both batteries operate independently
- [ ] Entity names distinguish batteries
- [ ] Can remove one battery without affecting other

## Phase 3: BLE Proxy

- [ ] Connection works through ESP32 proxy
- [ ] Range extends as expected
- [ ] Sensors update through proxy
- [ ] Commands work through proxy
- [ ] Connection maintains when moving between direct/proxy

## Phase 4: Energy Dashboard

- [ ] Battery Power In/Out sensors available
- [ ] Can add to Energy Dashboard as battery
- [ ] Energy tracking accumulates correctly
- [ ] Historical data displays in Energy tab
