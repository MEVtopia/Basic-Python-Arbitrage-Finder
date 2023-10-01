This repository contains a script to identify arbitrage opportunities on the Ethereum blockchain.

The script searches through all V2 Uniswap fork pools and predicts arbitrage opportunities for the next block.

1. Fetches all pools from V2 Uniswap forks.
2. Analyzes reserves to determine potential arbitrage opportunities.
3. Filters out opportunities that are not profitable due to factors like gas costs.

1. Ensure you have the required dependencies installed (e.g., web3).
2. Set up your Ethereum node URLs in `nodes_details.txt`, one https:// each line.
3. Run the script: `python oppFinder.py`

This is a simple tool and might not capture all nuances of real-world arbitrage on Ethereum. Always use with caution and do your own research.
