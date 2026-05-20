struct Student
{
    char* name;
    int num;
    int age;
    float score;
};
void main(){
    int i, num_140 = 0;
    float sum = 0;
    int flag = 0;
    Student sts[2]={{"Li ping", 5, 18, 145.0},
                    {"Wang ming", 6, 18, 150.0}};
    if(sts[1].score<140) flag=-1;
    else flag=1;
    printf("%d ", flag);
}