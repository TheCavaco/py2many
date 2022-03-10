from typing import List


def sum_two(x, y):
    return x + y

def get_first(container):
    return container[0]

if __name__ == "__main__":
    assert sum_two(1, 2) == 3
    assert sum_two(1, 2.0) == 3.0
    assert sum_two("1", "2") == "12"
    assert get_first([1,2,3]) == 1
    assert get_first(["1","2","3"]) == "1"
    assert get_first(["1",2,3]) == "1"
    assert get_first("123") == "1"