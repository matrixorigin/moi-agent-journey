CREATE TABLE orders(id TEXT PRIMARY KEY, customer TEXT, amount REAL, status TEXT);
CREATE TABLE items(id TEXT PRIMARY KEY, order_id TEXT, sku TEXT, qty INTEGER);