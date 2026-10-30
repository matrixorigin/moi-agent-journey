def total(qty, price, discount=0):
    if qty < 0 or price < 0:
        raise ValueError("negative input")
    return qty * price * (1 - discount // 100)

if __name__ == '__main__':
    print(total(2, 100, 10))
