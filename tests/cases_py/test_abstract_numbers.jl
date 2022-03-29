using Test
#= Unit tests for numbers.py. =#



abstract type AbstractTestNumbers end

mutable struct TestNumbers <: AbstractTestNumbers

end
function test_int(self::AbstractTestNumbers)
    @test Int64 <: Integer
    @test Int64 <: Complex
    @test (7 == real(Int(7)))
    @test (0 == imag(Int(7)))
    @test (7 == conj(Int(7)))
    @test (-7 == conj(Int(-7)))
    @test (7 == numerator(Int(7)))
    @test (1 == denominator(Int(7)))
end

function test_float(self::AbstractTestNumbers)
    @test !(Float64 <: Rational)
    @test Float64 <: Real
    @test (7.3 == real(float(7.3)))
    @test (0 == imag(float(7.3)))
    @test (7.3 == conjugate(float(7.3)))
    @test (-7.3 == conjugate(float(-7.3)))
end

function test_complex(self::AbstractTestNumbers)
    @test !(Complex <: Real)
    @test Complex <: Complex
    c1, c2 = (complex(3, 2), complex(4, 1))
    @test_throws TypeError math.trunc(c1)
    @test_throws TypeError operator.mod(c1, c2)
    @test_throws TypeError divmod(c1, c2)
    @test_throws TypeError operator.floordiv(c1, c2)
    @test_throws TypeError float(c1)
    @test_throws TypeError int(c1)
end

function main()
    main()
end

main()
