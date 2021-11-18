import copy
from easydict import EasyDict as edict
import json
import requests
from web3 import Web3
import csv
from retry import retry
from flask import Flask, render_template, request, send_file
from concurrent.futures.thread import ThreadPoolExecutor
from decimal import Decimal


app = Flask(__name__)


configs = {}
results_wallets = {}
result_list = []
total_lp = 0
total_tokens = 0


@app.route('/', methods=['POST', 'GET'])
def index():
    flag = bool(False)
    copy_result = []
    if request.method == 'POST':
        f = request.files['config_file']
        f.save(f.filename)
        set_config(f.filename)
        f = request.files['csv_file']
        f.save(f.filename)
        check_addresses(f.filename)
        copy_result = copy.deepcopy(result_list)
        result_list.clear()
        results_wallets.clear()
        flag = bool(True)
    return render_template('base.html', data=copy_result, flag=flag)


@app.route('/download-config')
def download_config():
    p = 'config/config.json'
    return send_file(p, as_attachment=True)


@app.route('/download-output')
def download_output():
    p = 'output.csv'
    return send_file(p, as_attachment=True)


@retry(exceptions=Exception, tries=3, delay=2, jitter=2)
def get_holders_list(contract: str):
    try:
        result = requests.get(f'https://api.covalenthq.com/v1/{configs.chain_id}/tokens/'
                              f'{contract}/token_holders/?&key={configs.covalent_api_key}')
        if result.status_code == 200:
            result = result.json()
            total_holders = int(result['data']['pagination']['total_count'])
            params = {'page-size': total_holders}
            result = requests.get(f'https://api.covalenthq.com/v1/{configs.chain_id}/tokens/'
                                  f'{contract}/token_holders/?&key={configs.covalent_api_key}', params=params)
            if result.status_code == 200:
                result = result.json()
                return result['data']['items']
            else:
                print(f'Something wrong. Status Code: {result.status_code}  ###get holders###')
                return []
        else:
            print(f'Something wrong. Status Code: {result.status_code}  ###get count of holders###')
            return {}
    except Exception as e:
        print(f'Somthing wrong from get_holders_list Function.')
        return []


def check_balances(wallet: str, holders: list):
    global total_tokens
    for item in holders:  # get balance from holders list
        if item['address'].lower() == configs.LP_contract.lower():
            total_tokens = Web3.fromWei(int(item['balance']), 'ether')
        if wallet.lower() == item['address'].lower():
            coins = Web3.fromWei(int(item['balance']), 'ether')
            results_wallets[wallet]['balance'] = coins if coins > 50 else 0
            break


def check_lp(wallet: str, holders: dict):
    for item in holders:
        if item['address'] == wallet.lower():
            user_lp = Web3.fromWei(int(item['balance']), 'ether')
            results_wallets[wallet]['lp'] = 2 * user_lp * total_tokens / total_lp
            break


def check_addresses(path: str):
    print('########## Starting Process ##########')
    global total_lp
    print('########## [1/4] Starting Get Holders ##########')
    holders_list = get_holders_list(configs.contract_address)
    print('########## [2/4] Starting Get LP Holders ##########')
    holders_lp_list = get_holders_list(configs.LP_contract)
    total_lp = Web3.fromWei(int(holders_lp_list[0]['total_supply']), 'ether')
    with open(path, mode='r', encoding="utf8") as inp:
        reader = csv.reader(inp)
        next(reader, None)  # skip on the header
        workers = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            print('########## [3/4] Starting Check Balances + LP ##########')
            for row in reader:  # get all wallets from CSV file
                wallet = row[3]
                if Web3.isAddress(wallet):  # check if wallet is address
                    if wallet in results_wallets:  # if wallet is duplicate
                        continue
                    init_result_dict(wallet)
                    check_balances(wallet, holders_list)  # set holders balance
                    check_lp(wallet, holders_lp_list)
                    workers.append(executor.submit(stack_of_function, wallet))
                else:
                    # print(f'The "{wallet}" address is not correct!')
                    pass
        print('########## [4/4] Starting Check Staking ##########')
        for worker in workers:
            worker.result()
        total_of_values()
        results_dict_to_list()
        download_output_file()
        print('########## Completed ##########')


def total_of_values():
    for wallet in results_wallets:
        sum = Decimal(results_wallets[wallet]['balance']) + Decimal(results_wallets[wallet]['lp']) + Decimal(results_wallets[wallet]['staking_1']) + Decimal(results_wallets[wallet]['staking_2']) + Decimal(results_wallets[wallet]['staking_3'])
        results_wallets[wallet]['total'] = sum


def init_result_dict(wallet: str):
    results_wallets[wallet] = {}
    results_wallets[wallet]['balance'] = 0.0
    results_wallets[wallet]['lp'] = 0.0
    results_wallets[wallet]['staking_1'] = 0.0
    results_wallets[wallet]['staking_2'] = 0.0
    results_wallets[wallet]['staking_3'] = 0.0
    results_wallets[wallet]['total'] = 0.0


@retry(exceptions=Exception, tries=3, delay=2, jitter=2)
def stack_of_function(wallet: str):
    try:
        w3 = Web3(Web3.HTTPProvider(configs.provider))
        count = 1
        for stake in configs.stakings:
            contract_instance = w3.eth.contract(address=stake, abi=configs.abi)
            temp = Web3.toChecksumAddress(wallet)
            res = contract_instance.functions.stakeOf(temp).call()
            results_wallets[wallet][f'staking_{count}'] = Web3.fromWei(int(res), 'ether')
            count += 1
    except Exception as e:
        print(f'Error Staking! {stake}')
        print(f'Error in stack_of_function ### {e}')


def download_output_file():
    try:
        fields = ['wallet', 'balance', 'lp', 'staking_1', 'staking_2', 'staking_3', 'total']
        with open('output.csv', 'w', newline='', encoding="utf8") as f:
            write = csv.writer(f)
            write.writerow(fields)
            write.writerows(result_list)
    except IOError:
        print("I/O error")


def results_dict_to_list():
    for data in results_wallets:
        wallet = data
        balance = results_wallets[data]['balance']
        lp = results_wallets[data]['lp']
        staking_1 = results_wallets[data]['staking_1']
        staking_2 = results_wallets[data]['staking_2']
        staking_3 = results_wallets[data]['staking_3']
        total = results_wallets[data]['total']
        line = [wallet, balance, lp, staking_1, staking_2, staking_3, total]
        result_list.append(line)


def set_config(file: str):
    global configs
    with open(file) as f:
        data = json.loads(f.read())
    configs = edict(data)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=False)