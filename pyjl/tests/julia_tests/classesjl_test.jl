using Classes

@class mutable Person begin
    name::String
end

function get_name(self::AbstractPerson)
    return self.name
end

@class mutable Student <: Person begin
    student_number::Int
end

function get_name(self::AbstractStudent)
    return "$(self.student_number) - $(self.name)"
end

function main()
    p = Person("Michael Carmichael")
    s = Student("Michael Carmichael", 111111)
    @assert(get_name(p) == "Michael Carmichael")
    @assert(get_name(s) == "111111 - Michael Carmichael")
    println("OK")
end

main()

# Translated From:
# class Person:
#     def __init__(self, name::str):
#         self.name = name

#     def printname(self):
#         return self.name

# class Student(Person):
#     def __init__(self, name:str, student_number:int):
#         super().__init__(name)
#         self.student_number = student_number

#     def printname(self):
#         return f"{self.student_number} - {self.name}"