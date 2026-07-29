from campaigniq.importers.thinkorswim.csv_reader import CsvReader

reader = CsvReader()
rows = reader.read("tests/data/Account Trading History.csv")
trade_rows = reader.trade_history_rows(rows)

for i, value in enumerate(trade_rows[0]):
    print(f"{i:2}: {value!r}")
