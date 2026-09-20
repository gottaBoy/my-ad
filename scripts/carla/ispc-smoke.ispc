export void double_values(uniform float input[], uniform float output[], uniform int count)
{
    foreach (i = 0 ... count)
    {
        output[i] = input[i] * 2.0;
    }
}
