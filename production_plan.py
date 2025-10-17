
from flask import Flask, json, request, jsonify
import logging
from math import isclose

app = Flask(__name__)

# We define bassi generic logger
logging.basicConfig(filename='productionplan.log',
                    level=logging.INFO
                    )

logger = logging.getLogger(__name__)


def compute_cost(plant: dict, fuels: dict) -> float:
    """Compute marginal cost for a plant given fuels and efficiency.
    Cost defined by fuel_price / efficiency
    Rules used:
    - gasfired: uses 'gas(euro/MWh)' fuel value
    - turbojet: uses 'kerosine(euro/MWh)' fuel value
    - windturbine: cost = 0
    """
    ptype = plant.get('type')
    eff = plant.get('efficiency', 1.0)

    if ptype == 'windturbine':
        return 0.0
    elif ptype == 'gasfired':
        gas_price = fuels.get('gas(euro/MWh)')
        co2_price = fuels.get('co2(euro/ton)', 0)
        if gas_price is None:
            raise ValueError('Missing gas price in fuels')
        return gas_price / eff + 0.3 * co2_price #Taking CO2 pricing into account at 0.3 ton/MWh
    elif ptype == 'turbojet':
        kerosine_price = fuels.get('kerosine(euro/MWh)')
        if kerosine_price is None:
            raise ValueError('Missing kerosine price in fuels')
        return kerosine_price / eff
    else:
        return float('inf')


def obtain_power(plant: dict, fuels: dict) -> tuple:
    """Adjust pmax & pmin for wind turbines using wind% (from fuels['wind(%)']).
    For wind turbines, pmin is treated as 0 (they are not dispatchable below 0),
    and pmax = original_pmax * wind_percent/100.
    """
    if plant.get('type') != 'windturbine':
        return plant['pmin'], plant['pmax']
    pmax, pmin = get_windpowerplant_power(plant, fuels)
    return pmin, pmax

def get_windpowerplant_power(plant: dict, fuels: dict) -> tuple:
    windp = fuels.get('wind(%)')
    try:
        factor = max(0.0, min(100.0, float(windp))) / 100.0
    except Exception as e:
        raise ValueError('Invalid or missing wind(%) in fuels') from e
    pmax = plant['pmax'] * factor
    pmin = 0.0
    return pmax,pmin

def ensure_total_equals_load(load: float, running_plants: dict, response: list, rounded_total: float):
    """
    Ensure total rounding still equals load (within 0.1). 
    If not, adjust the cheapest plant accordingly.
    """
    difference = round(load - rounded_total, 1)
    if abs(difference) >= 0.1:
        # find cheapest non-wind plant where the delta can fit within pmin/pmax
        for plant in sorted(running_plants, key=lambda x: x['cost']):
            for r in response:
                if r['name'] == plant['name']:
                    new_p = r['power'] + difference
                    if new_p >= plant['pmin'] - 1e-9 and new_p <= plant['pmax'] + 1e-9 and new_p >= 0.0:
                        r['power'] = round(new_p, 1)
                        difference = 0.0
                    break
            if difference == 0.0:
                break

def process_plants(running_plants: list, plants: dict, fuels: dict)-> tuple:
    """Process plants to adjust pmin/pmax for wind turbines and compute costs."""
    total_max = 0.0
    total_min = 0.0
    for plant in plants:
        try:
            pmin, pmax = obtain_power(plant, fuels)
        except Exception as e:
            logger.exception('Error adjusting wind pmax')
            return jsonify({'error': str(e)}), 400

        running_plant = {
            'name': plant['name'],
            'type': plant['type'],
            'efficiency': plant.get('efficiency', 1.0),
            'pmin': float(pmin),
            'pmax': float(pmax),
            'cost': compute_cost(plant, fuels)
        }
        running_plants.append(running_plant)
        total_max += running_plant['pmax']
        total_min += running_plant['pmin']
    return total_min, total_max

        
@app.route('/productionplan', methods=['POST'])
def productionplan():
    payload = getattr(app, 'payload', None)
    try:
        load = float(payload['load'])
        plants = payload['powerplants']
        fuels = payload['fuels']
    except Exception as e:
        logger.exception('Some keys in payload could not be found.')
        return jsonify({'error': 'wrong payload format'}), 400
    running_plants = []
    total_min, total_max = process_plants(running_plants, plants, fuels)
    
    if load < total_min or load > total_max:
        msg = f'Requested load is loo large or too small! Load: {load}. Range: [{total_min}, {total_max}]'
        logger.error(msg)
        return jsonify({'error': msg}), 400

    # Initialize production to pmin
    for plant in running_plants:
        # plant['power'] = plant['pmin']
        plant['power'] = 0
        

    # Sort by ascending cost
    running_plants = sorted(running_plants, key=lambda x: x['cost'])
    remaining_total_load = load - sum(it['power'] for it in running_plants)

    # We loop over plants in crescient cost order
    for plant in running_plants:
        if remaining_total_load <= 1e-9:
            break
        available = plant['pmax'] - plant['power']
        to_add = min(available, remaining_total_load)
        plant['power'] += to_add
        remaining_total_load -= to_add

    # If remaining > 0, try redistribution
    # If remaining < 0 (we overshot due to pmin constraints), reduce from most expensive
    # We'll perform iterative adjustments: reduce from expensive ones, increase cheaper ones
    max_iter = 1000
    iter_count = 0
    minimum_difference = 1e-6
    if not isclose(sum(it['power'] for it in running_plants), load, rel_tol=0, abs_tol=0.5) and iter_count < max_iter:
        logger.warning('Algorithm did not reach exact load; final_total=%s target=%s', final_total, load)
        
    final_total = optional_precision_iteration(load, running_plants, max_iter, iter_count, minimum_difference)
    if not isclose(final_total, load, rel_tol=0, abs_tol=0.5):
        logger.warning('Algorithm did not reach exact load; final_total=%s target=%s', final_total, load)

    # Round productions to one decimal like in example responses
    response = []
    for plant in running_plants:
        response.append({'name': plant['name'], 'power': plant['power']})

    rounded_total = sum(r['power'] for r in response)
    ensure_total_equals_load(load, running_plants, response, rounded_total)

    final_rounded_total = sum(r['power'] for r in response)
    if not isclose(final_rounded_total, load, rel_tol=0, abs_tol=0.5):
        msg = f'ERROR! Unable to match load precisely after rounding: rounded_total={final_rounded_total}, target={load}'
        logger.warning(msg)

    logger.info('Production plan computed successfully for load %s', load)
    return jsonify(response), 200

def optional_precision_iteration(load, running_plants, max_iter, iter_count, minimum_difference):
    while not isclose(sum(it['power'] for it in running_plants), load, rel_tol=0, abs_tol=0.5) and iter_count < max_iter:
        total = sum(it['power'] for it in running_plants)
        diff = total - load
        if abs(diff) < minimum_difference:
            break

        if diff > 0:
            # Need to reduce production by diff.
            # Reduce from most expensive plants first (but keep >= pmin)
            for plant in sorted(running_plants, key=lambda x: x['cost'], reverse=True):
                if diff <= minimum_difference:
                    break
                reducible = plant['power'] - plant['pmin']
                reduce_by = min(reducible, diff)
                if reduce_by > 0:
                    plant['power'] -= reduce_by
                    diff -= reduce_by
        else:
            # Need to increase production (-diff). Add to cheapest plants with spare capacity
            need = -diff
            for plant in sorted(running_plants, key=lambda x: x['cost']):
                if need <= minimum_difference:
                    break
                spare = plant['pmax'] - plant['power']
                add = min(spare, need)
                if add > 0:
                    plant['power'] += add
                    need -= add

        iter_count += 1
    final_total = sum(it['power'] for it in running_plants)
    return final_total

if __name__ == '__main__':
    data_path = 'example_payloads/payload3.json'
    try:
        app.payload = json.load(open(data_path, 'r'))
    except Exception as e:
        logger.exception('Failed to parse data file')
        
    app.run(host='localhost', port=8888, debug=True)
