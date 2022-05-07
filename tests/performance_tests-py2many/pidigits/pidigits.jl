using Printf

function extract_Digit(nth)::Any
    global tmp1, tmp2, acc, den, num
    tmp1 = num * nth
    tmp2 = tmp1 + acc
    tmp1 = tmp2 ÷ den
    return tmp1
end

function eliminate_Digit(d)
    global acc, den, num
    acc = acc - den * d
    acc = acc * 10
    num = num * 10
end

function next_Term(k)
    global acc, den, num
    k2 = k * 2 + 1
    acc = acc + num * 2
    acc = acc * k2
    den = den * k2
    num = num * k
end

function main()
    global tmp1, tmp2, acc, den, num
    n = parse(Int, argv[2])
    tmp1 = 0
    tmp2 = 0
    acc = 0
    den = 1
    num = 1
    i = 0
    k = 0
    while i < n
        k += 1
        next_Term(k)
        if num > acc
            continue
        end
        d = extract_Digit(3)
        if d != extract_Digit(4)
            continue
        end

        i += 1
        if (i % 10) == 0
            @printf("\t:%d", i)
        end
        eliminate_Digit(d)
    end
end

main()
