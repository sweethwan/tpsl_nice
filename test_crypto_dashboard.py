import asyncio
import logging
import unittest

import pandas as pd

from crypto_logics import CryptoLogic
from main_nice import TpslApp


class FakeExchange:
    def __init__(self):
        self.balance_params = None
        self.order_calls = []

    def fetch_balance(self, params):
        self.balance_params = params
        return {
            'total': {'USDT': 100.0, 'BTC': None, 'ETH': 0.0},
            'USDT': {'free': 25.0, 'used': 75.0},
            'BTC': {'free': 0.0, 'used': 0.0},
            'ETH': {'free': 0.0, 'used': 0.0},
        }

    def fetch_positions(self):
        return [
            {
                'symbol': 'BTC/USDT:USDT', 'contracts': 2, 'side': 'long',
                'entryPrice': 100.0, 'markPrice': 110.0, 'notional': 220.0,
                'unrealizedPnl': 20.0, 'settle': 'USDT', 'linear': True,
                'type': 'swap', 'hedged': True,
            },
            {
                'symbol': 'ETH/USDC:USDC', 'contracts': 1, 'side': 'short',
                'entryPrice': 10.0, 'markPrice': 9.0, 'notional': 9.0,
                'unrealizedPnl': 1.0, 'settle': 'USDC', 'linear': True,
                'type': 'swap',
            },
            {
                'symbol': 'BTC/USDT:USDT-OPTION', 'contracts': 1, 'side': 'long',
                'settle': 'USDT', 'linear': True, 'type': 'option',
            },
        ]

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.order_calls.append((symbol, order_type, side, amount, price, params))
        return {'id': 'close-order'}


def logic_with(exchange):
    logic = CryptoLogic.__new__(CryptoLogic)
    logic.exchange_id = 'binance'
    logic.exchange = exchange
    logic.logger = logging.getLogger('test.crypto')
    return logic


class FakeDashboardClient:
    def fetch_balance(self, account_type):
        return pd.DataFrame([
            {'Symbol': 'USDT', 'Free': 100.0, 'Used': 0.0, 'Total': 100.0, 'AvgPrice': 0.0},
            {'Symbol': 'BTC', 'Free': 0.1, 'Used': 0.0, 'Total': 0.1, 'AvgPrice': 20000.0},
        ])

    def fetch_positions(self):
        return pd.DataFrame([
            {
                'Symbol': 'BTC/USDT:USDT', 'Total': 2.0, 'AvgPrice': 20000.0,
                'MarkPrice': 21000.0, 'Notional': 42000.0,
                'UnrealizedPnl': 2000.0, 'ContractSize': 1.0,
                'Side': 'short', 'Hedged': True,
            },
        ])

    def fetch_ticker(self, symbol):
        return 20000.0


class CryptoLogicTests(unittest.TestCase):
    def test_fetch_balance_uses_requested_account_and_skips_none(self):
        exchange = FakeExchange()
        dataframe = logic_with(exchange).fetch_balance('future')

        self.assertEqual(exchange.balance_params, {'type': 'future'})
        self.assertEqual(dataframe['Symbol'].tolist(), ['USDT'])
        self.assertEqual(dataframe.iloc[0]['Used'], 75.0)

    def test_fetch_positions_keeps_only_usdt_linear_futures(self):
        dataframe = logic_with(FakeExchange()).fetch_positions()

        self.assertEqual(len(dataframe), 1)
        position = dataframe.iloc[0]
        self.assertEqual(position['Symbol'], 'BTC/USDT:USDT')
        self.assertEqual(position['Side'], 'long')
        self.assertEqual(position['Notional'], 220.0)

    def test_close_futures_position_uses_reduce_only_and_opposite_side(self):
        exchange = FakeExchange()
        order = logic_with(exchange).close_futures_position(
            'BTC/USDT:USDT', 2, 'short', hedged=True
        )

        self.assertEqual(order['id'], 'close-order')
        self.assertEqual(
            exchange.order_calls,
            [('BTC/USDT:USDT', 'market', 'buy', 2.0, None, {'reduceOnly': True, 'hedged': True})],
        )


class DashboardRowsTests(unittest.TestCase):
    def test_wallet_and_futures_rows_are_distinct_without_notional_double_counting(self):
        dashboard = TpslApp()
        dashboard.running = False
        client = FakeDashboardClient()

        wallet_rows, wallet_total = asyncio.run(
            dashboard._build_balance_rows(
                'binance', client, 'spot', 'spot', '현물', 1000.0
            )
        )
        futures_rows = asyncio.run(
            dashboard._build_futures_rows('binance', client, 1000.0)
        )

        self.assertEqual([row['kind'] for row in wallet_rows], ['현물 현금', '현물'])
        self.assertEqual(wallet_total, 2_100_000.0)
        self.assertEqual(futures_rows[0]['kind'], '선물 포지션 · SHORT')
        self.assertEqual(futures_rows[0]['pnl_val'], -5.0)
        self.assertEqual(futures_rows[0]['revenue'], 2_000_000.0)


if __name__ == '__main__':
    unittest.main()
