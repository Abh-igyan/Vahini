from fastapi import FastAPI
app = FastAPI()
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/orderbook")
def get_book(): return {"bids": [], "asks": [], "trades": []}
@app.post("/order")
def place_order(order: dict): return {"status": "placed"}
@app.post("/reset")
def reset(): return {"status": "reset"}
@app.delete("/order/{order_id}")
def delete_order(order_id: str): return {"status": "deleted"}

