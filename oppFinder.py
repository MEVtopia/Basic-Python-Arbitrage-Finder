from web3 import Web3, AsyncHTTPProvider
from web3.eth import AsyncEth
import asyncio
import json
import nest_asyncio
import math

nest_asyncio.apply()

# Load node details
node_urls = []
with open("nodes_details.txt", "r") as file:
    node_urls = [line.strip() for line in file]

# Initialize web3 providers
w3 = Web3(Web3.HTTPProvider(node_urls[0]))
sync_providers = [Web3.HTTPProvider(url) for url in node_urls]
async_providers = [Web3(AsyncHTTPProvider(url), modules={"eth": (AsyncEth)}) for url in node_urls]

# Load factory contract addresses
with open("addressUniV2Fork.json", "r") as file:
    factory_addresses = json.load(file)

# Factory ABI definition
contract_abi = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "token0", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "token1", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "pair", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "", "type": "uint256"},
        ],
        "name": "PairCreated",
        "type": "event",
    }
]

# Define a function to recursively retrieve events in progressively smaller intervals
def retrieve_pair_events(contract_instance, start_block, end_block):
    current_end_block = end_block
    total_event_count = 0

    # Inner function to recursively fetch events in smaller block intervals
    def fetch_events_in_chunks(contract, block_from, block_to):
        try:
            fetched_events = (
                contract.events.PairCreated()
                .create_filter(fromBlock=block_from, toBlock=block_to)
                .get_all_entries()
            )
            print(f"Retrieving events... {block_from} to {block_to}")
            nonlocal total_event_count
            total_event_count += len(fetched_events)
            return fetched_events
        except ValueError:
            print(f"ValueError: {block_from} to {block_to}")
            midpoint_block = (block_from + block_to) // 2
            return fetch_events_in_chunks(contract, block_from, midpoint_block) + fetch_events_in_chunks(
                contract, midpoint_block + 1, block_to
            )

    return fetch_events_in_chunks(contract_instance, start_block, current_end_block)

# Gather pool data for each factory contract
pool_data_collection = []
for factory_label, factory_details in factory_addresses.items():
    retrieved_events = retrieve_pair_events(
        w3.eth.contract(address=factory_details["factory"], abi=contract_abi),
        0,
        w3.eth.block_number,
    )
    print(f"Retrieved {len(retrieved_events)} pools for {factory_label}")
    for event in retrieved_events:
        pool_data_collection.append(
            {
                "token0": event["args"]["token0"],
                "token1": event["args"]["token1"],
                "pair": event["args"]["pair"],
                "factory": factory_label,
            }
        )

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2" #We are looking only for WETH/ pairs

# Construct a dictionary for pairs containing WETH
pair_pool_mapping = {}
for pair_data in pool_data_collection:
    current_pair = (pair_data['token0'], pair_data['token1'])
    if WETH_ADDRESS not in current_pair:
        continue
    pair_pool_mapping.setdefault(current_pair, []).append(pair_data)

# Filter out pairs that don't have at least two pools, as it wouldn't be possible to arbitrage
final_pool_mapping = {pair: pools for pair, pools in pair_pool_mapping.items() if len(pools) >= 2}

# Pairs count
print(f'Total different pairs: {len(final_pool_mapping)}.')

# Pools count
print(f'Total pools: {sum(len(pools) for pools in final_pool_mapping.values())}.')

# Pair with maximum number of pools
most_pooled_pair = max(final_pool_mapping, key=lambda k: len(final_pool_mapping[k]))
print(f'Pair with maximum pools: {most_pooled_pair} has {len(final_pool_mapping[most_pooled_pair])} pools.')

# Pools per pair distribution: deciles
pool_counts = sorted([len(pools) for pools in final_pool_mapping.values()], reverse=True)
print(f'Distribution of pools per pair (deciles): {pool_counts[::len(pool_counts)//10]}.')

# Pools per pair distribution: percentiles
print(f'Distribution of pools per pair (percentiles): {pool_counts[::len(pool_counts)//100][:10]}.')

# Basic contract deployed on Ethereum. It takes a list of Uniswap V2 pair addresses and returns the reserves for each pair.
QUERY_CONTRACT_ADDR = "0x6c618c74235c70DF9F6AD47c6b5E9c8D3876432B" 
QUERY_ABI =  [{"inputs": [
            {
                "internalType": "contract IUniswapV2Pair[]",
                "name": "_pairs",
                "type": "address[]",
            }
        ],
        "name": "getReservesByPairs",
        "outputs": [
            {"internalType": "uint256[3][]", "name": "", "type": "uint256[3][]"}
        ],
        "stateMutability": "view",
        "type": "function"}]


# Asynchronous function to get reserves in parallel #fast
async def fetchReservesInParallel(pair_addresses, node_providers, partition_size=1000):
    # Instantiate contract objects for each provider
    contract_instances = [
        node_provider.eth.contract(address=QUERY_CONTRACT_ADDR, abi=QUERY_ABI)
        for node_provider in node_providers
    ]

    # Split the addresses into chunks
    address_chunks = [
        [address for address in pair_addresses[start : start + partition_size]]
        for start in range(0, len(pair_addresses), partition_size)
    ]

    # Create asynchronous tasks for each chunk
    async_tasks = [
        contract_instances[idx % len(contract_instances)].functions.getReservesByPairs(chunk_addresses).call()
        for idx, chunk_addresses in enumerate(address_chunks)
    ]

    # Execute tasks concurrently
    aggregated_results = await asyncio.gather(*async_tasks)

    # Combine results into a single list
    combined_results = [item for sublist in aggregated_results for item in sublist]

    return combined_results

# Utility functions for calculating optimal trade inputs and profits
def single_swap_output(amount, reserve_a, reserve_b, swap_fee=0.003):
    return reserve_b * (1 - reserve_a / (reserve_a + amount * (1 - swap_fee)))

def gross_trade_profit(amount, rsv1, rsv2, swap_fee=0.003):
    ra1, rb1 = rsv1
    ra2, rb2 = rsv2
    return single_swap_output(single_swap_output(amount, ra1, rb1, swap_fee), rb2, ra2, swap_fee) - amount

def best_trade_input(rsv1, rsv2, swap_fee=0.003):
    ra1, rb1 = rsv1
    ra2, rb2 = rsv2
    return (math.sqrt(ra1 * rb1 * ra2 * rb2 * (1 - swap_fee)**4 * (rb1 * (1 - swap_fee) + rb2)**2) - ra1 * rb2 * (1 - swap_fee) * (rb1 * (1 - swap_fee) + rb2)) / ((1 - swap_fee) * (rb1 * (1 - swap_fee) + rb2))**2

# Get reserves for each pool in the pool dictionary
pools_to_query = [pool_obj["pair"] for pair, pool_list in final_pool_mapping.items() for pool_obj in pool_list]
print(f"Fetching reserves for {len(pools_to_query)} pools...")

def find_potential_trades() :    
    fetched_reserves = asyncio.get_event_loop().run_until_complete(fetchReservesInParallel(pools_to_query, async_providers))
    opportunity_idx = 0
    potential_trades = []
    for pair, pool_group in final_pool_mapping.items():
        for pool_obj in pool_group:
            pool_obj["reserves"] = fetched_reserves[opportunity_idx]
            opportunity_idx += 1

        for source_pool in pool_group:
            for target_pool in pool_group:
                if source_pool["pair"] == target_pool["pair"] or 0 in source_pool["reserves"] or 0 in target_pool["reserves"]:
                    continue

                if source_pool["token0"] == WETH_ADDRESS:
                    src_reserves = (source_pool["reserves"][0], source_pool["reserves"][1])
                    tgt_reserves = (target_pool["reserves"][0], target_pool["reserves"][1])
                else:
                    src_reserves = (source_pool["reserves"][1], source_pool["reserves"][0])
                    tgt_reserves = (target_pool["reserves"][1], target_pool["reserves"][0])

                optimal_input = best_trade_input(src_reserves, tgt_reserves)
                if optimal_input < 0:
                    continue

                gross_profit = gross_trade_profit(optimal_input, src_reserves, tgt_reserves)
                potential_trades.append({
                    "profit": gross_profit / 1e18,
                    "input": optimal_input / 1e18,
                    "pair": pair,
                    "source_pool": source_pool,
                    "target_pool": target_pool,
                })
    return potential_trades
potential_trades = find_potential_trades()
print(f"Identified {len(potential_trades)} trade opportunities.")

import requests

def get_eth_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
    response = requests.get(url)
    data = response.json()
    return data['ethereum']['usd']

def find_profitable_trades(real_trades):    
    current_gas_price = w3.eth.gas_price
    for opportunity in real_trades:
        opportunity["actual_profit"] = opportunity["profit"] - 107000 * current_gas_price / 1e18 # 107k gas is the lower bound price for 2 Univ2 swap + sending a transaction on Ethereum

    # Rank by actual net profit
    real_trades.sort(key=lambda item: item["actual_profit"], reverse=True)

    # Retain profitable opportunities
    profitable_opportunities = [opportunity for opportunity in real_trades if opportunity["actual_profit"] > 0]

    ### Display statistics
    # Positive opportunities
    print(f"Number of probitable opportunities for this block : {len(profitable_opportunities)}.")

    # Detailed info on each opportunity
    CURRENT_ETH_PRICE = get_eth_price()
    for opportunity in profitable_opportunities:
        print(f"Profit: {opportunity['actual_profit']} ETH (${opportunity['actual_profit'] * CURRENT_ETH_PRICE})")
        print(f"Input: {opportunity['input']} ETH (${opportunity['input'] * CURRENT_ETH_PRICE})")
        print(f"Source Pool: {opportunity['source_pool']['pair']}")
        print(f"Destination Pool: {opportunity['target_pool']['pair']}")
        print()

# Convert list of opportunities into a set of unique identifiers based on source and target pools
def opps_to_set(opps):
    return set((opp['source_pool']['pair'], opp['target_pool']['pair']) for opp in opps)

import time

# Initialize last block number
last_block_number = w3.eth.block_number
print(last_block_number)

while True:
    current_block_number = w3.eth.block_number

    # Check if a new block has been mined
    if current_block_number > last_block_number:
        print(f"New block detected: {current_block_number}")

        # Place your logic here
        new_potential_trades = find_potential_trades()
        print(f"found" , len(new_potential_trades) , "new trades")
        old_opps_set = opps_to_set(potential_trades)
        new_opps_set = opps_to_set(new_potential_trades)

        # Find opportunities that are unique to the new set
        unique_new_opps_set = new_opps_set - old_opps_set

        # Convert back to list of opportunities
        real_trades = [opp for opp in new_potential_trades if (opp['source_pool']['pair'], opp['target_pool']['pair']) in unique_new_opps_set]
        find_profitable_trades(real_trades)

        # Update the last block number
        last_block_number = current_block_number
        

    time.sleep(6)  # Sleep for 6 seconds before checking again, since each block takes ~12 seconds to mine no need to query all the time.
    
