function generator_func()
c_generator_func = Channel(3)
num = 1
put!(c_generator_func, num);
num = 5
put!(c_generator_func, num);
num = 10
put!(c_generator_func, num);
close(c_generator_func)
return c_generator_func
end

function generator_func_loop()
c_generator_func_loop = Channel(1)
num = 0
t_generator_func_loop = @async for n in (0:2)
put!(c_generator_func_loop, num + n);
end
bind(c_generator_func_loop, t_generator_func_loop)
end

function generator_func_loop_using_var()
c_generator_func_loop_using_var = Channel(1)
num = 0
end_ = 2
end_ = 3
t_generator_func_loop_using_var = @async for n in (0:end_ - 1)
put!(c_generator_func_loop_using_var, num + n);
end
bind(c_generator_func_loop_using_var, t_generator_func_loop_using_var)
end

function generator_func_nested_loop()
c_generator_func_nested_loop = Channel(1)
t_generator_func_nested_loop = @async for n in (0:1)
for i in (0:1)
put!(c_generator_func_nested_loop, (n, i));
end
end
bind(c_generator_func_nested_loop, t_generator_func_nested_loop)
end

function file_reader(file_name::String)
c_file_reader = Channel(1)
t_file_reader = @async for file_row in readline(file_name)
put!(c_file_reader, file_row);
end
bind(c_file_reader, t_file_reader)
end

function testgen()
c_testgen = Channel(2)
println("first");
put!(c_testgen, 1);
println("second");
put!(c_testgen, 2);
close(c_testgen)
return c_testgen
end

function fib()
c_fib = Channel(1)
a = 0
b = 1
while true
put!(c_fib, a);
a, b = (b, a + b)
end
bind(c_fib, t_fib)
end

struct TestClass
end
function generator_func(self::TestClass)
c_generator_func = Channel(3)
num = 123
put!(c_generator_func, num);
num = 5
put!(c_generator_func, num);
num = 10
put!(c_generator_func, num);
close(c_generator_func)
return c_generator_func
end

function main()
arr1 = []
for i in generator_func()
push!(arr1, i);
end
@assert(arr1 == [1, 5, 10])
arr2 = []
for i in generator_func_loop()
push!(arr2, i);
end
@assert(arr2 == [0, 1, 2])
arr3 = []
for i in generator_func_loop_using_var()
push!(arr3, i);
end
@assert(arr3 == [0, 1, 2])
arr4 = []
testClass1::TestClass = TestClass()
for i in generator_func(testClass1)
push!(arr4, i);
end
@assert(arr4 == [123, 5, 10])
arr5 = []
for i in generator_func_nested_loop()
push!(arr5, i);
end
@assert(arr5 == [(0, 0), (0, 1), (1, 0), (1, 1)])
arr6 = []
for res in file_reader("C:/Users/Miguel Marcelino/Desktop/test.txt")
push!(arr6, res);
end
@assert(arr6 == ["test\n", "test\n", "test"])
arr7 = []
res = fib()
for i in (0:5)
push!(arr7, __next__(res));
end
@assert(arr7 == [0, 1, 1, 2, 3, 5])
for i in testgen()
println(i);
end
end

main()
